import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const errorRate = new Rate('custom_error_rate');
const criticalErrorRate = new Rate('critical_error_rate');
const ttfbTrend = new Trend('time_to_first_byte');

// URL cible modifiable via la variable d'environnement TARGET_URL
const RAW_BASE_URL = __ENV.TARGET_URL || 'https://integralcarte.regies.tech/';
const BASE_URL = RAW_BASE_URL.replace(/\/+$/, ''); // Supprime le slash final pour éviter les '//'

const PRESENT_ROUTES = [
  // Public - routes critiques de premier ordre pour l'expérience client
  { name: 'home', path: '/', expectedStatus: [200], method: 'GET', weight: 24, critical: true },
  { name: 'home_alt', path: '/s', expectedStatus: [200, 302], method: 'GET', weight: 2, critical: false },
  { name: 'catalogue', path: '/catalogue/', expectedStatus: [200], method: 'GET', weight: 12, critical: true },
  { name: 'supports', path: '/supports/', expectedStatus: [200], method: 'GET', weight: 12, critical: true },
  { name: 'support_detail', path: '/support/00000000-0000-0000-0000-000000000000/', expectedStatus: [200, 404], method: 'GET', weight: 2, critical: false },
  { name: 'services', path: '/services/', expectedStatus: [200], method: 'GET', weight: 8, critical: true },
  { name: 'contact', path: '/contact/', expectedStatus: [200], method: 'GET', weight: 8, critical: true },
  { name: 'contact_confirmation', path: '/contact/confirmation/', expectedStatus: [200, 302, 404], method: 'GET', weight: 2, critical: false },
  { name: 'reserver', path: '/reserver/', expectedStatus: [200, 302], method: 'GET', weight: 3, critical: true },
  { name: 'reserver_etape2', path: '/reserver/etape2/', expectedStatus: [200, 302], method: 'GET', weight: 2, critical: true },
  { name: 'reserver_etape3', path: '/reserver/etape3/', expectedStatus: [200, 302], method: 'GET', weight: 2, critical: true },
  { name: 'confirmation', path: '/confirmation/00000000-0000-0000-0000-000000000000/', expectedStatus: [200, 404], method: 'GET', weight: 2, critical: false },
  { name: 'suivi', path: '/suivi/', expectedStatus: [200, 302], method: 'GET', weight: 2, critical: false },
  { name: 'geojson_api', path: '/api/geojson/', expectedStatus: [200], method: 'GET', weight: 8, critical: true },
  { name: 'check_dispo_api', path: '/api/check-dispo/', expectedStatus: [200, 400, 403], method: 'POST', weight: 6, critical: true },
  { name: 'contact_form', path: '/contact/envoyer/', expectedStatus: [200, 400, 403, 405], method: 'POST', weight: 4, critical: false },

  // Gestion / comptes
  { name: 'gestion_admin', path: '/gestion/admin/', expectedStatus: [200, 302], method: 'GET', weight: 2 },
  { name: 'gestion_accounts_login', path: '/gestion/accounts/login/', expectedStatus: [200, 302], method: 'GET', weight: 3 },
  { name: 'gestion_accounts_logout', path: '/gestion/accounts/logout/', expectedStatus: [200, 302], method: 'GET', weight: 2 },
  { name: 'gestion_accounts_profile', path: '/gestion/accounts/profile/', expectedStatus: [200, 302], method: 'GET', weight: 2 },
  { name: 'gestion_accounts_users', path: '/gestion/accounts/users/', expectedStatus: [200, 302], method: 'GET', weight: 4 },
  { name: 'gestion_accounts_user_create', path: '/gestion/accounts/users/creer/', expectedStatus: [200, 302], method: 'GET', weight: 2 },

  // Inventory
  { name: 'gestion_inventory_root', path: '/gestion/inventory/', expectedStatus: [200, 302], method: 'GET', weight: 4 },
  { name: 'gestion_inventory_support_create', path: '/gestion/inventory/ajouter/', expectedStatus: [200, 302], method: 'GET', weight: 3 },
  { name: 'gestion_inventory_support_list', path: '/gestion/inventory/maintenances/', expectedStatus: [200, 302], method: 'GET', weight: 3 },
  { name: 'gestion_inventory_formats', path: '/gestion/inventory/formats/', expectedStatus: [200, 302], method: 'GET', weight: 2 },
  { name: 'gestion_inventory_marches', path: '/gestion/inventory/marches/', expectedStatus: [200, 302], method: 'GET', weight: 2 },
  { name: 'gestion_inventory_api_geojson', path: '/gestion/inventory/api/geojson/', expectedStatus: [200, 302], method: 'GET', weight: 2 },
  { name: 'gestion_inventory_support_next_code', path: '/gestion/inventory/supports/next-code/', expectedStatus: [200, 302, 405], method: 'GET', weight: 1 },

  // Campaigns
  { name: 'gestion_campaigns_dashboard', path: '/gestion/campaigns/', expectedStatus: [200, 302], method: 'GET', weight: 6 },
  { name: 'gestion_campaigns_reservation_list', path: '/gestion/campaigns/reservations/', expectedStatus: [200, 302], method: 'GET', weight: 3 },
  { name: 'gestion_campaigns_clients', path: '/gestion/campaigns/clients/', expectedStatus: [200, 302], method: 'GET', weight: 3 },
  { name: 'gestion_campaigns_campaigns_list', path: '/gestion/campaigns/campaigns/', expectedStatus: [200, 302], method: 'GET', weight: 4 },
  { name: 'gestion_campaigns_api_disponibilite', path: '/gestion/campaigns/api/disponibilite/', expectedStatus: [200, 302, 400], method: 'GET', weight: 2 },
  { name: 'gestion_campaigns_demandes', path: '/gestion/campaigns/demandes/', expectedStatus: [200, 302], method: 'GET', weight: 2 },

  // Planning
  { name: 'gestion_planning_calendrier', path: '/gestion/planning/calendrier/', expectedStatus: [200, 302], method: 'GET', weight: 2 },
  { name: 'gestion_planning_main_courante', path: '/gestion/planning/main-courante/', expectedStatus: [200, 302], method: 'GET', weight: 2 },
  { name: 'gestion_planning_api_taux', path: '/gestion/planning/api/taux/', expectedStatus: [200, 302], method: 'GET', weight: 1 },

  // Reports
  { name: 'gestion_reports_index', path: '/gestion/reports/', expectedStatus: [200, 302], method: 'GET', weight: 2 },
  { name: 'gestion_reports_supports', path: '/gestion/reports/supports/', expectedStatus: [200, 302], method: 'GET', weight: 2 },
  { name: 'gestion_reports_supports_export_pdf', path: '/gestion/reports/supports/export/pdf/', expectedStatus: [200, 302], method: 'GET', weight: 1 },
  { name: 'gestion_reports_supports_export_excel', path: '/gestion/reports/supports/export/excel/', expectedStatus: [200, 302], method: 'GET', weight: 1 },
];

const ROUTES = PRESENT_ROUTES;

function pickRoute() {
  const total = ROUTES.reduce((sum, route) => sum + route.weight, 0);
  let r = Math.random() * total;

  for (const route of ROUTES) {
    r -= route.weight;
    if (r <= 0) return route;
  }
  return ROUTES[0];
}

const PARAMS = {
  headers: {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'fr-FR,fr;q=0.9',
    'Connection': 'keep-alive',
  },
  timeout: '45s',
};

export const options = {
  stages: [
    { duration: '15s', target: 5 },
    { duration: '30s', target: 15 },
    { duration: '10s', target: 0 },
  ],
  thresholds: {
    critical_error_rate: ['rate<0.02'],
    custom_error_rate: ['rate<0.04'],
    http_req_failed: ['rate<0.08'],
    http_req_duration: ['p(95)<1500'],
    time_to_first_byte: ['p(95)<1200'],
  },
};

export default function () {
  const route = pickRoute();
  const normalizedPath = route.path.startsWith('/') ? route.path : `/${route.path}`;
  const url = `${BASE_URL}${normalizedPath}`;

  let res;
  if (route.method === 'POST') {
    const body = route.name === 'check_dispo_api'
      ? JSON.stringify({
          faces: ['00000000-0000-0000-0000-000000000000'],
          date_debut: '2026-09-11',
          date_fin: '2026-09-12',
        })
      : JSON.stringify({ message: 'test' });

    res = http.post(url, body, {
      ...PARAMS,
      headers: {
        ...PARAMS.headers,
        'Content-Type': 'application/json',
      },
    });
  } else {
    res = http.get(url, PARAMS);
  }

  ttfbTrend.add(res.timings.waiting);

  const allowed = route.expectedStatus.includes(res.status);
  const success = check(res, {
    [`${route.name}: statut attendu`]: () => allowed,
    [`${route.name}: réponse non vide`]: (r) => !!r.body && r.body.length > 0,
    [`${route.name}: temps de réponse < 2s`]: (r) => r.timings.duration < 2000,
  });

  errorRate.add(!success);
  if (route.critical) {
    criticalErrorRate.add(!success);
  }
  sleep(Math.random() * 1 + 1);
}