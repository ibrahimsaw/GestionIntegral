from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from accounts.decorators import StaffRequiredMixin
from campaigns.models import Campagne

from .drive_piges import (
    DrivePigesError,
    build_photo_filename,
    build_photo_prefix,
    delete_photo,
    get_drive_service,
    get_next_photo_number,
    get_or_create_campaign_folder,
    upload_photo,
)
from .forms import PigeFichierMetadataForm, PigesUploadForm
from .models import PigeFichier


class PigesUploadView(StaffRequiredMixin, View):
    template_name = 'piges/upload.html'

    def get_campagne(self, campagne_pk):
        return get_object_or_404(
            Campagne.objects.select_related('client'),
            pk=campagne_pk,
        )

    def get(self, request, campagne_pk):
        campagne = self.get_campagne(campagne_pk)
        form = PigesUploadForm(campagne=campagne)
        return render(
            request,
            self.template_name,
            {'campagne': campagne, 'form': form},
        )

    def post(self, request, campagne_pk):
        campagne = self.get_campagne(campagne_pk)
        form = PigesUploadForm(
            request.POST,
            request.FILES,
            campagne=campagne,
        )
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {'campagne': campagne, 'form': form},
            )

        ligne = form.cleaned_data['ligne']
        support = ligne.support or (ligne.face.support if ligne.face_id else None)
        if support:
            support_name = support.nom
            city = support.ville
        else:
            support_name = f"{ligne.emplacement.marche.nom} {ligne.emplacement.code}"
            city = ligne.emplacement.marche.ville
        photo_date = form.cleaned_data['date_prise']
        photos = form.cleaned_data['photos']

        try:
            service = get_drive_service()
            folder = get_or_create_campaign_folder(
                service,
                campagne.client.nom,
                campagne.reference,
                campagne.lien_piges,
            )
        except DrivePigesError as exc:
            form.add_error(None, str(exc))
            return render(
                request,
                self.template_name,
                {'campagne': campagne, 'form': form},
                status=503,
            )

        prefix = build_photo_prefix(city, support_name, photo_date)
        uploaded_count = 0
        upload_error = None
        for uploaded_file in photos:
            try:
                sequence_number = get_next_photo_number(
                    service,
                    folder.folder_id,
                    prefix,
                )
                filename = build_photo_filename(prefix, sequence_number)
                drive_file_id = upload_photo(
                    service,
                    folder.folder_id,
                    uploaded_file,
                    filename,
                )
                PigeFichier.objects.create(
                    campagne=campagne,
                    ligne=ligne,
                    support=support,
                    support_nom=support_name,
                    ville=city or '',
                    date_prise=photo_date,
                    nom_fichier=filename,
                    drive_file_id=drive_file_id,
                    ajoute_par=request.user,
                )
                uploaded_count += 1
            except DrivePigesError as exc:
                upload_error = f"{uploaded_file.name} : {exc}"
                break

        if uploaded_count and not campagne.lien_piges:
            campagne.lien_piges = folder.web_view_link
            campagne.save(update_fields=['lien_piges'])

        if uploaded_count == len(photos):
            messages.success(
                request,
                f"{uploaded_count} photo(s) envoyée(s) dans le dossier Google Drive de la campagne.",
            )
            return redirect('campagne_detail', pk=campagne.pk)

        if uploaded_count:
            messages.warning(
                request,
                f"{uploaded_count} photo(s) envoyée(s). L'envoi a été interrompu : {upload_error}",
            )
            return redirect('campagne_detail', pk=campagne.pk)

        form.add_error(None, upload_error or "Aucune photo n'a pu être envoyée.")
        return render(
            request,
            self.template_name,
            {'campagne': campagne, 'form': form},
            status=503,
        )


class PigeFichierUpdateView(StaffRequiredMixin, View):
    template_name = 'piges/photo_form.html'

    def get_pige(self, campagne_pk, pige_pk):
        return get_object_or_404(
            PigeFichier.objects.select_related('campagne'),
            pk=pige_pk,
            campagne_id=campagne_pk,
        )

    def get(self, request, campagne_pk, pige_pk):
        pige = self.get_pige(campagne_pk, pige_pk)
        return render(request, self.template_name, {
            'pige': pige,
            'campagne': pige.campagne,
            'form': PigeFichierMetadataForm(instance=pige),
        })

    def post(self, request, campagne_pk, pige_pk):
        pige = self.get_pige(campagne_pk, pige_pk)
        form = PigeFichierMetadataForm(request.POST, instance=pige)
        if form.is_valid():
            form.save()
            messages.success(request, "La date de prise de vue de la pige a été modifiée.")
            return redirect(f"{reverse('campagne_detail', kwargs={'pk': pige.campagne_id})}#piges")
        return render(request, self.template_name, {
            'pige': pige,
            'campagne': pige.campagne,
            'form': form,
        })


class PigeFichierDeleteView(StaffRequiredMixin, View):
    template_name = 'piges/photo_confirm_delete.html'

    def get_pige(self, campagne_pk, pige_pk):
        return get_object_or_404(
            PigeFichier.objects.select_related('campagne'),
            pk=pige_pk,
            campagne_id=campagne_pk,
        )

    def get(self, request, campagne_pk, pige_pk):
        pige = self.get_pige(campagne_pk, pige_pk)
        return render(request, self.template_name, {
            'pige': pige,
            'campagne': pige.campagne,
        })

    def post(self, request, campagne_pk, pige_pk):
        pige = self.get_pige(campagne_pk, pige_pk)
        try:
            service = get_drive_service()
            delete_photo(service, pige.drive_file_id)
        except DrivePigesError as exc:
            return render(request, self.template_name, {
                'pige': pige,
                'campagne': pige.campagne,
                'error': str(exc),
            }, status=503)

        campagne_id = pige.campagne_id
        with transaction.atomic():
            pige.delete()
        messages.success(request, "La photo a été supprimée de Google Drive et des piges de la campagne.")
        return redirect(f"{reverse('campagne_detail', kwargs={'pk': campagne_id})}#piges")
