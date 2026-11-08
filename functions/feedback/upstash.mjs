import { createHash } from 'node:crypto';

const DEFAULT_HASH_KEY = 'theseus:session-search:feedback:v0';
const HASH_KEY = /^[A-Za-z0-9:._-]{1,128}$/;

function normalizedFeedback(input) {
  return {
    baseline_request_ref: input.baseline_request_ref,
    result_request_ref: input.result_request_ref ?? null,
    failure_class: input.failure_class,
    operator: {
      name: input.operator?.name,
      version: input.operator?.version,
    },
    outcome: input.outcome,
    notes: input.notes ?? null,
  };
}

function feedbackRef(feedback) {
  return createHash('sha256')
    .update('theseus.session-search-feedback-record.v0\n')
    .update(JSON.stringify(feedback))
    .digest('hex')
    .slice(0, 32);
}

function normalizeConfig({ restUrl, token, hashKey }) {
  if (typeof restUrl !== 'string' || typeof token !== 'string' || token.length < 1) {
    throw new Error('FEEDBACK_STORE_CONFIG_INVALID');
  }
  let url;
  try {
    url = new URL(restUrl);
  } catch {
    throw new Error('FEEDBACK_STORE_URL_INVALID');
  }
  if (
    url.protocol !== 'https:'
    || url.username
    || url.password
    || url.search
    || url.hash
  ) {
    throw new Error('FEEDBACK_STORE_URL_INVALID');
  }
  const key = hashKey ?? DEFAULT_HASH_KEY;
  if (typeof key !== 'string' || !HASH_KEY.test(key)) {
    throw new Error('FEEDBACK_STORE_KEY_INVALID');
  }
  return { restUrl: url.href, token, hashKey: key };
}

export function createUpstashFeedbackWriter({
  restUrl,
  token,
  hashKey = DEFAULT_HASH_KEY,
  fetchImpl = fetch,
  now = () => new Date(),
}) {
  if (typeof fetchImpl !== 'function' || typeof now !== 'function') {
    throw new Error('FEEDBACK_STORE_CONFIG_INVALID');
  }
  const config = normalizeConfig({ restUrl, token, hashKey });

  return async function writeFeedback(input) {
    const feedback = normalizedFeedback(input);
    const ref = feedbackRef(feedback);
    const observedAt = now();
    if (!(observedAt instanceof Date) || Number.isNaN(observedAt.getTime())) {
      throw new Error('FEEDBACK_STORE_CLOCK_INVALID');
    }
    const record = {
      schema: 'theseus.session-search-feedback-record.v0',
      feedback_ref: ref,
      received_at: observedAt.toISOString(),
      feedback,
    };

    let response;
    try {
      response = await fetchImpl(config.restUrl, {
        method: 'POST',
        headers: {
          authorization: 'Bearer ' + config.token,
          'content-type': 'application/json',
        },
        body: JSON.stringify([
          'HSETNX',
          config.hashKey,
          ref,
          JSON.stringify(record),
        ]),
      });
    } catch {
      throw new Error('FEEDBACK_STORE_FAILED');
    }

    if (!response?.ok) {
      throw new Error('FEEDBACK_STORE_FAILED');
    }

    let payload;
    try {
      payload = await response.json();
    } catch {
      throw new Error('FEEDBACK_STORE_RESPONSE_INVALID');
    }
    if (payload?.error != null) {
      throw new Error('FEEDBACK_STORE_FAILED');
    }

    const inserted = payload?.result === 1 || payload?.result === true;
    const duplicate = payload?.result === 0 || payload?.result === false;
    if (!inserted && !duplicate) {
      throw new Error('FEEDBACK_STORE_RESPONSE_INVALID');
    }

    return {
      status: inserted ? 'RECORDED' : 'ALREADY_RECORDED',
      feedback_ref: ref,
    };
  };
}

export function createUpstashFeedbackWriterFromEnv({
  env = process.env,
  fetchImpl = fetch,
  now = () => new Date(),
} = {}) {
  const restUrl = env.SESSION_SEARCH_FEEDBACK_UPSTASH_REST_URL;
  const token = env.SESSION_SEARCH_FEEDBACK_UPSTASH_REST_TOKEN;
  if (!restUrl && !token) return null;
  if (!restUrl || !token) throw new Error('FEEDBACK_STORE_CONFIG_INCOMPLETE');

  return createUpstashFeedbackWriter({
    restUrl,
    token,
    hashKey: env.SESSION_SEARCH_FEEDBACK_UPSTASH_HASH_KEY ?? DEFAULT_HASH_KEY,
    fetchImpl,
    now,
  });
}
