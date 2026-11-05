import { timingSafeEqual } from 'node:crypto';

export function hasValidBearerToken(request, expectedToken) {
  if (typeof expectedToken !== 'string' || expectedToken.length === 0) {
    throw new Error('HTTP_AUTH_TOKEN_MISSING');
  }

  const header = request.headers.get('authorization') ?? '';
  const match = /^Bearer ([^\s]+)$/i.exec(header);
  if (!match) return false;

  const actual = Buffer.from(match[1], 'utf8');
  const expected = Buffer.from(expectedToken, 'utf8');
  if (actual.length !== expected.length) return false;
  return timingSafeEqual(actual, expected);
}
