function runEjsEnvironmentSchemaPreflightV1() {
  return assertEjsTestEnvironmentV1();
}

function runEjsIdempotencyLockSmokeV1() {
  assertEjsTestEnvironmentV1();
  const requestId = 'TEST-001B-' + Utilities.getUuid();
  const payloadA = {operation: 'echo', environment: 'TEST', n: 1};
  const payloadB = {operation: 'echo', environment: 'TEST', n: 2};
  try {
    const first = claimEjsRequestV1(requestId, payloadA);
    const retry = claimEjsRequestV1(requestId, payloadA);
    let collisionRejected = false;
    try { claimEjsRequestV1(requestId, payloadB); } catch (err) { collisionRejected = String(err).indexOf('EJS_REQUEST_ID_PAYLOAD_COLLISION') >= 0; }
    return {
      passed: first.state === 'claimed' && retry.state === 'exact_retry' && collisionRejected,
      writeExecuted: false,
      firstState: first.state,
      retryState: retry.state,
      collisionRejected
    };
  } finally {
    clearEjsTestRequestV1_(requestId);
  }
}

function runEjsCloudRunHealthEchoV1() {
  assertEjsTestEnvironmentV1();
  const health = callEjsCloudRunV1_('/health', null);
  const requestId = 'TEST-001C-' + Utilities.getUuid();
  const payload = {
    requestId,
    executionId: 'exec:' + requestId,
    contractVersion: EJS_CONFIG_V1.CONTRACT_VERSION,
    environment: 'TEST',
    operation: 'echo',
    authority: {browserRead: true, formWrite: false, fileUpload: false, finalSubmit: false},
    trace: {test: 'TEST-001C'}
  };
  const echo = callEjsCloudRunV1_('/v1/execute', payload);
  return {passed: health.status === 200 && echo.status === 200, writeExecuted: false, healthStatus: health.status, echoStatus: echo.status};
}
