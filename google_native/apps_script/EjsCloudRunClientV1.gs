function getEjsInvokerIdTokenV1_() {
  const cfg = getEjsRuntimeConfigV1_();
  if (!cfg.cloudRunUrl) throw new Error('EJS_CLOUD_RUN_URL_REQUIRED');
  if (!cfg.invokerServiceAccount) throw new Error('EJS_INVOKER_SA_REQUIRED');

  const name = 'projects/-/serviceAccounts/' + encodeURIComponent(cfg.invokerServiceAccount);
  const endpoint = 'https://iamcredentials.googleapis.com/v1/' + name + ':generateIdToken';
  const response = UrlFetchApp.fetch(endpoint, {
    method: 'post',
    contentType: 'application/json',
    headers: {Authorization: 'Bearer ' + ScriptApp.getOAuthToken()},
    payload: JSON.stringify({audience: cfg.cloudRunUrl, includeEmail: true}),
    muteHttpExceptions: true
  });
  if (response.getResponseCode() !== 200) {
    throw new Error('EJS_ID_TOKEN_GENERATION_FAILED:' + response.getResponseCode());
  }
  const parsed = JSON.parse(response.getContentText());
  if (!parsed.token) throw new Error('EJS_ID_TOKEN_MISSING');
  return parsed.token;
}

function callEjsCloudRunV1_(path, payload) {
  assertEjsTestEnvironmentV1();
  const cfg = getEjsRuntimeConfigV1_();
  const idToken = getEjsInvokerIdTokenV1_();
  const url = cfg.cloudRunUrl.replace(/\/$/, '') + path;
  const options = {
    method: payload === null ? 'get' : 'post',
    headers: {Authorization: 'Bearer ' + idToken},
    muteHttpExceptions: true
  };
  if (payload !== null) {
    options.contentType = 'application/json';
    options.payload = JSON.stringify(payload);
  }
  const response = UrlFetchApp.fetch(url, options);
  return {status: response.getResponseCode(), body: response.getContentText()};
}
