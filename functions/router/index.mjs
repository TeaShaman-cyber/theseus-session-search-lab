import { makeRouterHandler } from './handler.mjs';

const rawCatalog = process.env.SESSION_SEARCH_CATALOG;
if (!rawCatalog) throw new Error('SESSION_SEARCH_CATALOG_MISSING');

let catalog;
try {
  catalog = JSON.parse(rawCatalog);
} catch {
  throw new Error('SESSION_SEARCH_CATALOG_INVALID');
}
if (!Array.isArray(catalog) || catalog.length < 1) {
  throw new Error('SESSION_SEARCH_CATALOG_EMPTY');
}

export default makeRouterHandler({ catalog });
