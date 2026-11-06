// Run with: node --test tests/films.test.mjs
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { BRANDS, filmType, films, firstStock, isStock, isos, stockName } from '../sprocket/static/films.js';

test('every brand has films, and every film a type and speeds', () => {
  assert.ok(BRANDS.length > 0);
  assert.equal(new Set(BRANDS).size, BRANDS.length);
  for (const brand of BRANDS) {
    assert.ok(films(brand).length > 0, brand);
    for (const film of films(brand)) {
      assert.ok(['color-negative', 'slide', 'bw-negative'].includes(filmType(brand, film)), film);
      assert.ok(isos(brand, film).length > 0 && isos(brand, film).every((n) => Number.isInteger(n) && n > 0), film);
    }
  }
});

test('the film list depends on the brand, and the speeds on the film', () => {
  assert.ok(films('Kodak').includes('Portra'));
  assert.ok(!films('Ilford').includes('Portra'));
  assert.deepEqual(isos('Kodak', 'Portra'), [160, 400, 800]);
  assert.deepEqual(isos('Kodak', 'Nope'), []);
  assert.deepEqual(films('Nobody'), []);
});

test('film types', () => {
  assert.equal(filmType('Kodak', 'Portra'), 'color-negative');
  assert.equal(filmType('Fujifilm', 'Velvia'), 'slide');
  assert.equal(filmType('Ilford', 'HP5 Plus'), 'bw-negative');
  assert.equal(filmType('Kodak', 'Nope'), null);
});

test('checking a remembered stock', () => {
  assert.ok(isStock({ brand: 'Kodak', film: 'Portra', iso: 400 }));
  assert.ok(!isStock({ brand: 'Kodak', film: 'Portra', iso: 100 }));
  assert.ok(!isStock({ brand: 'Kodak', film: 'Portra', iso: '400' }));
  assert.ok(!isStock(null));
});

test('choosing a brand or film picks its first speed', () => {
  assert.deepEqual(firstStock('Ilford'), { brand: 'Ilford', film: films('Ilford')[0], iso: isos('Ilford', films('Ilford')[0])[0] });
  assert.deepEqual(firstStock('Kodak', 'Portra'), { brand: 'Kodak', film: 'Portra', iso: 160 });
  assert.equal(firstStock('Nobody'), null);
});

test('stock names', () => {
  assert.equal(stockName({ brand: 'Kodak', film: 'Portra', iso: 400 }), 'Kodak Portra 400');
  assert.equal(stockName({ brand: 'Fujifilm', film: 'Velvia', iso: 50 }), 'Fujifilm Velvia 50');
  assert.equal(stockName({ brand: 'CineStill', film: '800T', iso: 800 }), 'CineStill 800T');
  assert.equal(stockName({ brand: 'Fujifilm', film: 'Provia 100F', iso: 100 }), 'Fujifilm Provia 100F');
  assert.equal(stockName({ brand: 'Ilford', film: 'XP2 Super', iso: 400 }), 'Ilford XP2 Super 400');
  assert.equal(stockName(null), '');
});
