function ejsCanonicalizeV1_(value) {
  if (Array.isArray(value)) return value.map(ejsCanonicalizeV1_);
  if (value && typeof value === 'object') {
    const out = {};
    Object.keys(value).sort().forEach(k => out[k] = ejsCanonicalizeV1_(value[k]));
    return out;
  }
  return value;
}

function ejsSha256HexV1_(text) {
  const bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, text, Utilities.Charset.UTF_8);
  return bytes.map(b => ('0' + ((b + 256) % 256).toString(16)).slice(-2)).join('');
}

function ejsRequestFingerprintV1_(payload) {
  return ejsSha256HexV1_(JSON.stringify(ejsCanonicalizeV1_(payload)));
}

function claimEjsRequestV1(requestId, payload) {
  if (!requestId) throw new Error('EJS_REQUEST_ID_REQUIRED');
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const props = PropertiesService.getScriptProperties();
    const key = 'EJS_REQ_V1_' + ejsSha256HexV1_(requestId).slice(0, 24);
    const fingerprint = ejsRequestFingerprintV1_(payload);
    const prior = props.getProperty(key);
    if (!prior) {
      props.setProperty(key, fingerprint);
      return {state: 'claimed', requestId, fingerprint};
    }
    if (prior === fingerprint) return {state: 'exact_retry', requestId, fingerprint};
    throw new Error('EJS_REQUEST_ID_PAYLOAD_COLLISION');
  } finally {
    lock.releaseLock();
  }
}

function clearEjsTestRequestV1_(requestId) {
  const key = 'EJS_REQ_V1_' + ejsSha256HexV1_(requestId).slice(0, 24);
  PropertiesService.getScriptProperties().deleteProperty(key);
}
