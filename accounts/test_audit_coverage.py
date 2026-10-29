from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import RequestFactory, TransactionTestCase

from accounts.audit import log_action
from accounts.models import AuditLog
from accounts.middleware import AuditRequestMiddleware
from inventory.models import FormatSupport, Maintenance, Support


class RequestAuditCoverageTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='audit-requester',
            password='test-password',
            role='admin',
        )

    def run_as_request(self, callback):
        request = RequestFactory().post('/')
        request.user = self.user
        middleware = AuditRequestMiddleware(callback)
        middleware(request)

    def test_model_create_update_delete_are_logged_with_requester_and_diffs(self):
        created = {}

        def mutate(_request):
            fmt = FormatSupport.objects.create(code='AUD-1', dimensions='4m x 3m')
            created['pk'] = fmt.pk
            fmt.dimensions = '5m x 3m'
            fmt.save()
            log_action(
                _request,
                AuditLog.ACTION_UPDATE,
                AuditLog.MODULE_INVENTORY,
                obj=fmt,
            )
            fmt.delete()
            log_action(
                _request,
                AuditLog.ACTION_DELETE,
                AuditLog.MODULE_INVENTORY,
                obj=fmt,
            )

        self.run_as_request(mutate)

        logs = list(AuditLog.objects.filter(object_id=created['pk']))
        self.assertEqual({entry.action for entry in logs}, {
            AuditLog.ACTION_CREATE, AuditLog.ACTION_UPDATE, AuditLog.ACTION_DELETE,
        })
        self.assertTrue(all(entry.user_id == self.user.pk for entry in logs))
        update = next(entry for entry in logs if entry.action == AuditLog.ACTION_UPDATE)
        self.assertEqual(update.module, AuditLog.MODULE_INVENTORY)
        self.assertEqual(update.changes['dimensions'], {
            'avant': '4m x 3m',
            'apres': '5m x 3m',
        })

    def test_queryset_mutations_and_explicit_log_do_not_duplicate_entries(self):
        created = {}

        def mutate(_request):
            fmt = FormatSupport.objects.create(code='AUD-2', dimensions='4m x 3m')
            created['pk'] = fmt.pk
            log_action(
                _request,
                AuditLog.ACTION_CREATE,
                AuditLog.MODULE_INVENTORY,
                obj=fmt,
            )
            FormatSupport.objects.filter(pk=fmt.pk).update(dimensions='6m x 3m')
            extra = FormatSupport(code='AUD-3', dimensions='2m x 2m')
            FormatSupport.objects.bulk_create([extra])
            created['bulk_pk'] = extra.pk

        self.run_as_request(mutate)

        create_logs = AuditLog.objects.filter(
            object_id=created['pk'],
            action=AuditLog.ACTION_CREATE,
        )
        self.assertEqual(create_logs.count(), 1)
        self.assertEqual(create_logs.get().user_id, self.user.pk)
        update = AuditLog.objects.get(
            object_id=created['pk'],
            action=AuditLog.ACTION_UPDATE,
        )
        self.assertEqual(update.changes['dimensions']['avant'], '4m x 3m')
        self.assertEqual(update.changes['dimensions']['apres'], '6m x 3m')
        self.assertTrue(AuditLog.objects.filter(
            object_id=created['bulk_pk'],
            action=AuditLog.ACTION_CREATE,
            user=self.user,
        ).exists())

    def test_auditlog_writes_are_not_audited(self):
        def create_auditlog(_request):
            AuditLog.objects.create(
                user=self.user,
                action=AuditLog.ACTION_VIEW,
                module=AuditLog.MODULE_USERS,
            )

        self.run_as_request(create_auditlog)
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_auth_group_changes_are_logged_in_the_auth_module(self):
        created = {}

        def mutate(_request):
            group = Group.objects.create(name='audit-group-before')
            created['pk'] = group.pk
            group.name = 'audit-group-after'
            group.save()

        self.run_as_request(mutate)

        logs = AuditLog.objects.filter(object_id=created['pk'])
        self.assertEqual(logs.count(), 2)
        self.assertTrue(all(log.user_id == self.user.pk for log in logs))
        update = logs.get(action=AuditLog.ACTION_UPDATE)
        self.assertEqual(update.module, AuditLog.MODULE_AUTH)
        self.assertEqual(
            update.changes['name'],
            {'avant': 'audit-group-before', 'apres': 'audit-group-after'},
        )

    def test_maintenance_side_effects_use_the_requester_not_the_assigned_technician(self):
        technician = get_user_model().objects.create_user(
            username='audit-assigned-technician',
            password='test-password',
            role='technicien',
        )
        support = Support.objects.create(
            code='AUD-MAINT',
            nom='Support audit maintenance',
            type_support=Support.TYPE_ECRAN,
            latitude=12.34,
            longitude=-1.52,
        )
        created = {}

        def mutate(_request):
            maintenance = Maintenance.objects.create(
                support=support,
                effectue_par=technician,
                etat_apres=Support.ETAT_PANNE,
                description='Arrêt constaté',
            )
            created['maintenance_pk'] = maintenance.pk

        self.run_as_request(mutate)

        maintenance_log = AuditLog.objects.get(
            action=AuditLog.ACTION_CREATE,
            object_id=created['maintenance_pk'],
        )
        self.assertEqual(maintenance_log.user_id, self.user.pk)
        support_log = AuditLog.objects.get(
            action=AuditLog.ACTION_UPDATE,
            object_id=support.pk,
        )
        self.assertEqual(support_log.user_id, self.user.pk)
        self.assertEqual(
            support_log.changes['etat']['avant'],
            Support.ETAT_BON,
        )
        self.assertEqual(
            support_log.changes['etat']['apres'],
            Support.ETAT_PANNE,
        )
