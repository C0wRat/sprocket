// Film stocks for the Brand / Film / ISO pickers. Choosing one sets the film type and
// names the stock in the saved files. No per-stock conversion data yet: see
// docs/research/film-profiles.md.

const C = 'color-negative';
const S = 'slide';
const B = 'bw-negative';

// [brand, [film, film type, [ISOs]], …]
const CATALOGUE = [
  ['Kodak', [
    ['ColorPlus', C, [200]],
    ['Ektachrome E100', S, [100]],
    ['Ektar', C, [100]],
    ['Gold', C, [200]],
    ['Portra', C, [160, 400, 800]],
    ['ProImage', C, [100]],
    ['T-Max', B, [100, 400]],
    ['T-Max P3200', B, [3200]],
    ['Tri-X', B, [400]],
    ['UltraMax', C, [400]],
  ]],
  ['Fujifilm', [
    ['Fujicolor 200', C, [200]],
    ['Neopan Acros II', B, [100]],
    ['Provia 100F', S, [100]],
    ['Superia X-TRA', C, [400]],
    ['Velvia', S, [50, 100]],
  ]],
  ['Ilford', [
    ['Delta', B, [100, 400, 3200]],
    ['FP4 Plus', B, [125]],
    ['HP5 Plus', B, [400]],
    ['Ortho Plus', B, [80]],
    ['Pan F Plus', B, [50]],
    ['SFX', B, [200]],
    ['XP2 Super', B, [400]],
  ]],
  ['Kentmere', [
    ['Pan', B, [100, 400]],
  ]],
  ['Harman', [
    ['Phoenix', C, [200]],
  ]],
  ['CineStill', [
    ['50D', C, [50]],
    ['400D', C, [400]],
    ['800T', C, [800]],
    ['BwXX', B, [250]],
  ]],
  ['Lomography', [
    ['Color Negative', C, [100, 400, 800]],
    ['Lady Grey', B, [400]],
  ]],
  ['Foma', [
    ['Fomapan', B, [100, 200, 400]],
  ]],
  ['Rollei', [
    ['RPX', B, [25, 100, 400]],
  ]],
];

export const BRANDS = CATALOGUE.map(([brand]) => brand);

const filmsOf = (brand) => CATALOGUE.find(([b]) => b === brand)?.[1] ?? [];

// The films a brand makes, by name.
export const films = (brand) => filmsOf(brand).map(([name]) => name);

// The speeds a film comes in.
export const isos = (brand, film) => filmsOf(brand).find(([name]) => name === film)?.[2] ?? [];

// The film type (color-negative, slide or bw-negative) of a film, or null if unknown.
export const filmType = (brand, film) => filmsOf(brand).find(([name]) => name === film)?.[1] ?? null;

// Is { brand, film, iso } a stock in the catalogue?
export const isStock = (s) => Boolean(s) && isos(s.brand, s.film).includes(s.iso);

// The first speed of the first film of `brand`, or of `film` if given: what the pickers
// show after choosing a brand or a film.
export function firstStock(brand, film = films(brand)[0]) {
  const iso = isos(brand, film)[0];
  return iso === undefined ? null : { brand, film, iso };
}

// "Kodak Portra 400". A speed that's already in the name ("Velvia 50") isn't repeated.
export function stockName(s) {
  if (!isStock(s)) return '';
  const name = `${s.brand} ${s.film}`;
  return s.film.includes(String(s.iso)) ? name : `${name} ${s.iso}`;
}
