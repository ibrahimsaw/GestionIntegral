class SortableListMixin:
    """
    Mixin générique pour ajouter un tri "façon Excel" à n'importe quelle ListView.
    Utilise deux paramètres GET séparés : 'sort' (nom de colonne) et 'dir' (asc/desc).
    """
    SORT_FIELDS = {}
    DEFAULT_SORT = 'pk'      # nom de colonne par défaut (sans signe)
    DEFAULT_DIR = 'asc'      # 'asc' ou 'desc'
    paginate_by = 20

    def get_current_sort(self):
        """Retourne (sort_key, sort_dir) à partir des paramètres GET."""
        sort_key = self.request.GET.get('sort', self.DEFAULT_SORT)
        sort_dir = self.request.GET.get('dir', self.DEFAULT_DIR)
        if sort_key not in self.SORT_FIELDS:
            sort_key = self.DEFAULT_SORT
        if sort_dir not in ('asc', 'desc'):
            sort_dir = self.DEFAULT_DIR
        return sort_key, sort_dir

    def apply_sort(self, qs):
        sort_key, sort_dir = self.get_current_sort()

        if sort_key in self.SORT_FIELDS:
            order_field = self.SORT_FIELDS[sort_key]
            if sort_dir == 'desc':
                order_field = f'-{order_field}'
            return qs.order_by(order_field)

        return qs.order_by(self.DEFAULT_SORT)

    def get_sort_context(self):
        sort_key, sort_dir = self.get_current_sort()

        base_params = self.request.GET.copy()
        sort_links = {}
        for key in self.SORT_FIELDS:
            params = base_params.copy()
            params['sort'] = key
            # Toggle : si la colonne est déjà active en 'asc' -> passe en 'desc', sinon 'asc'
            if sort_key == key and sort_dir == 'asc':
                params['dir'] = 'desc'
            else:
                params['dir'] = 'asc'
            params.pop('page', None)
            sort_links[key] = params.urlencode()

        return {
            'sort_key': sort_key,
            'sort_dir': sort_dir,
            'sort_links': sort_links,
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_sort_context())
        return context