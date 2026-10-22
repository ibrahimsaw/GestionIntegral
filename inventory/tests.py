from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.urls import reverse

from inventory.models import FormatSupport, Support
from inventory.views import FormatSupportDetailView, FormatSupportListView


class FormatSupportDetailViewTests(TestCase):
    def test_detail_shows_format_details_and_matching_supports(self):
        format_support = FormatSupport.objects.create(
            code='DETAIL-4X3',
            dimensions='4m × 3m',
            superficie=12,
            categorie='Standard',
        )
        support = Support.objects.create(
            code='DETAIL-SUPPORT',
            nom='Panneau du format',
            type_support=Support.TYPE_PANNEAU,
            format=format_support.code,
            latitude=12.34,
            longitude=-1.52,
            ville='Ouagadougou',
            quartier='Centre',
        )

        request = RequestFactory().get(reverse(
            'format_support_detail', args=[format_support.pk]
        ))
        view = FormatSupportDetailView()
        view.setup(request, pk=format_support.pk)
        view.object = format_support
        context = view.get_context_data(object=format_support)
        rendered = render_to_string(view.template_name, context, request=request)

        self.assertEqual(context['support_count'], 1)
        self.assertEqual(list(context['supports']), [support])
        self.assertIn('4m × 3m', rendered)
        self.assertIn('Panneau du format', rendered)

    def test_detail_supports_are_paginated(self):
        format_support = FormatSupport.objects.create(
            code='DETAIL-PAGED',
            dimensions='4m × 3m',
            categorie='Standard',
        )
        for number in range(21):
            Support.objects.create(
                code=f'DETAIL-PAGED-{number:02d}',
                nom=f'Panneau {number:02d}',
                type_support=Support.TYPE_PANNEAU,
                format=format_support.code,
                latitude=12.34,
                longitude=-1.52,
                ville='Ouagadougou',
            )

        request = RequestFactory().get(
            reverse('format_support_detail', args=[format_support.pk]),
            {'page': '2'},
        )
        view = FormatSupportDetailView()
        view.setup(request, pk=format_support.pk)
        view.object = format_support
        context = view.get_context_data(object=format_support)
        rendered = render_to_string(view.template_name, context, request=request)

        self.assertEqual(context['paginator'].count, 21)
        self.assertEqual(context['page_obj'].number, 2)
        self.assertEqual(len(context['supports']), 1)
        self.assertIn('Page 2 / 2', rendered)
        self.assertIn('?page=1', rendered)

    def test_format_list_is_paginated_and_reports_total_count(self):
        for number in range(21):
            FormatSupport.objects.create(
                code=f'LIST-PAGED-{number:02d}',
                dimensions='4m × 3m',
                categorie='Standard',
            )

        request = RequestFactory().get(reverse('format_list'), {'page': '2'})
        view = FormatSupportListView()
        view.setup(request)
        object_list = view.get_queryset()
        context = view.get_context_data(object_list=object_list)
        rendered = render_to_string(view.template_name, context, request=request)

        self.assertEqual(context['paginator'].count, 21)
        self.assertEqual(context['page_obj'].number, 2)
        self.assertIn('21 format', rendered)
        self.assertIn('Page 2 / 2', rendered)
