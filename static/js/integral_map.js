/**
 * ══════════════════════════════════════════════════════════════════
 * INTEGRAL MAP SYSTEM — MODULE CENTRALISÉ LEAFLET
 * Gère l'initialisation, les tuiles CartoDB / OSM (sans blocage 403),
 * les coordonnées par défaut et le redimensionnement automatique.
 * ══════════════════════════════════════════════════════════════════
 */
(function (window) {
  'use strict';

  // Configuration par défaut optimisée pour le Burkina Faso et sans blocage "ACCESS BLOCKED"
  window.INTEGRAL_MAP_CONFIG = {
    defaultCenter: [12.3714, -1.5197], // Ouagadougou
    defaultZoom: 12,
    maxZoom: 19,
    // Fournisseur CartoDB Voyager : aucun blocage "ACCESS BLOCKED", esthétique cartographique moderne
    tileUrl: 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',
    // Serveurs de secours en cas d'indisponibilité
    fallbackTileUrl: 'https://{s}.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png',
    subdomains: ['a', 'b', 'c', 'd'],
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions" target="_blank" rel="noopener">CARTO</a>'
  };

  window.INTEGRAL_MAP_DEFAULTS = {
    center: window.INTEGRAL_MAP_CONFIG.defaultCenter,
    zoom: window.INTEGRAL_MAP_CONFIG.defaultZoom
  };

  /**
   * Attache les tuiles de carte sans restriction "ACCESS BLOCKED"
   * @param {L.Map} map - L'instance de carte Leaflet
   * @param {Object} options - Options de configuration des tuiles
   * @returns {L.TileLayer}
   */
  window.buildSafeLeafletTiles = function (map, options = {}) {
    if (!map || typeof L === 'undefined') {
      console.warn('[IntegralMap] Leaflet non chargé ou carte invalide.');
      return null;
    }

    const tileUrl = options.tileUrl || window.INTEGRAL_MAP_CONFIG.tileUrl;
    const attribution = options.attribution || window.INTEGRAL_MAP_CONFIG.attribution;
    const maxZoom = options.maxZoom || window.INTEGRAL_MAP_CONFIG.maxZoom;
    const subdomains = options.subdomains || window.INTEGRAL_MAP_CONFIG.subdomains;

    // Création de la couche de tuiles haute disponibilité
    const tileLayer = L.tileLayer(tileUrl, {
      attribution: attribution,
      maxZoom: maxZoom,
      subdomains: subdomains
    });

    // Basculement automatique sur le serveur de secours si une tuile échoue
    tileLayer.on('tileerror', function (error, tile) {
      if (window.INTEGRAL_MAP_CONFIG.fallbackTileUrl && tile && tile.src && !tile.dataset.fallbackTried) {
        tile.dataset.fallbackTried = 'true';
        const sub = ['a', 'b', 'c'][Math.floor(Math.random() * 3)];
        tile.src = window.INTEGRAL_MAP_CONFIG.fallbackTileUrl
          .replace('{s}', sub)
          .replace('{z}', error.coords.z)
          .replace('{x}', error.coords.x)
          .replace('{y}', error.coords.y);
      }
    });

    tileLayer.addTo(map);

    // Ajustement de la vue si spécifié dans les options
    if (options.center) {
      const zoom = options.zoom !== undefined ? options.zoom : map.getZoom();
      map.setView(options.center, zoom);
    }

    // Déclenchement automatique de l'ajustement de taille
    setTimeout(() => {
      if (map && typeof map.invalidateSize === 'function') {
        map.invalidateSize();
      }
    }, 200);

    return tileLayer;
  };

  /**
   * Initialise une carte Leaflet complète avec tuiles intégrées en 1 seule ligne.
   * @param {string|HTMLElement} target - ID de l'élément DOM ou élément
   * @param {Object} mapOptions - Options Leaflet (center, zoom, zoomControl, etc.)
   * @returns {L.Map|null}
   */
  window.initIntegralMap = function (target, mapOptions = {}) {
    if (typeof L === 'undefined') {
      console.error('[IntegralMap] Bibliothèque Leaflet introuvable.');
      return null;
    }

    const el = typeof target === 'string' ? document.getElementById(target) : target;
    if (!el) {
      console.warn('[IntegralMap] Conteneur introuvable pour la carte:', target);
      return null;
    }

    const center = mapOptions.center || window.INTEGRAL_MAP_CONFIG.defaultCenter;
    const zoom = mapOptions.zoom !== undefined ? mapOptions.zoom : window.INTEGRAL_MAP_CONFIG.defaultZoom;

    // Nettoyage si le conteneur avait déjà une instance de carte
    if (el._leaflet_id && el._leaflet_map) {
      try {
        el._leaflet_map.remove();
      } catch (e) {
        console.warn('[IntegralMap] Nettoyage ancienne instance carte:', e);
      }
    }

    const map = L.map(el, {
      center: center,
      zoom: zoom,
      zoomControl: mapOptions.zoomControl !== undefined ? mapOptions.zoomControl : true,
      scrollWheelZoom: mapOptions.scrollWheelZoom !== undefined ? mapOptions.scrollWheelZoom : true,
      dragging: mapOptions.dragging !== undefined ? mapOptions.dragging : true,
      ...mapOptions
    });

    el._leaflet_map = map;

    // Ajout des tuiles fiables
    window.buildSafeLeafletTiles(map, mapOptions);

    return map;
  };

  // Écouteur global pour actualiser les cartes lors du redimensionnement de fenêtre
  window.addEventListener('resize', function () {
    const maps = document.querySelectorAll('.leaflet-container');
    maps.forEach(el => {
      if (el._leaflet_map && typeof el._leaflet_map.invalidateSize === 'function') {
        el._leaflet_map.invalidateSize();
      }
    });
  });

})(window);
