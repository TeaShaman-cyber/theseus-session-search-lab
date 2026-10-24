import { neon } from '@neondatabase/serverless';
import { makeShardHandler } from './handler.mjs';

const databaseUrl = process.env.SESSION_SEARCH_DATABASE_URL ?? process.env.DATABASE_URL;
if (!databaseUrl) throw new Error('DATABASE_URL_MISSING');

const sql = neon(databaseUrl);

export default makeShardHandler(async (call) => {
  const rows = await sql.query(call.text, call.values);
  const value = rows?.[0]?.result;
  if (!value) throw new Error('QUERY_RESULT_MISSING');
  return value;
});
