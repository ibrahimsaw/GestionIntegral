from django.utils import timezone
from .views import get_cached_alertes
def alerts_data(request):
    # ... votre logique ici ...
    return {'alertes': get_cached_alertes()}