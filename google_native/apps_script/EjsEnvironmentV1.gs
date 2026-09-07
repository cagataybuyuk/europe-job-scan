function assertEjsTestEnvironmentV1() {
  const cfg = getEjsRuntimeConfigV1_();
  if (cfg.environment !== 'TEST') throw new Error('EJS_ENVIRONMENT_NOT_TEST');
  if (cfg.targetSpreadsheetId === EJS_CONFIG_V1.PROD_SPREADSHEET_ID) throw new Error('EJS_PROD_TARGET_FORBIDDEN');
  if (cfg.targetSpreadsheetId !== EJS_CONFIG_V1.TEST_SPREADSHEET_ID) throw new Error('EJS_TEST_TARGET_MISMATCH');

  const ss = SpreadsheetApp.openById(cfg.targetSpreadsheetId);
  if (ss.getId() !== EJS_CONFIG_V1.TEST_SPREADSHEET_ID) throw new Error('EJS_TEST_ID_READBACK_MISMATCH');
  if (ss.getName() !== EJS_CONFIG_V1.TEST_SPREADSHEET_TITLE) throw new Error('EJS_TEST_TITLE_MISMATCH');
  if (ss.getSpreadsheetTimeZone() !== EJS_CONFIG_V1.TIME_ZONE) throw new Error('EJS_TIMEZONE_MISMATCH');

  const checked = {};
  EJS_CONFIG_V1.REQUIRED_SHEETS.forEach(name => {
    const sheet = ss.getSheetByName(name);
    if (!sheet) throw new Error('EJS_REQUIRED_SHEET_MISSING:' + name);
    checked[name] = true;
  });
  Object.keys(EJS_CONFIG_V1.REQUIRED_HEADERS).forEach(name => {
    const sheet = ss.getSheetByName(name);
    const width = sheet.getLastColumn();
    const headers = width ? sheet.getRange(1, 1, 1, width).getDisplayValues()[0] : [];
    EJS_CONFIG_V1.REQUIRED_HEADERS[name].forEach(header => {
      if (headers.indexOf(header) < 0) throw new Error('EJS_REQUIRED_HEADER_MISSING:' + name + ':' + header);
    });
  });
  return {
    passed: true,
    writeExecuted: false,
    environmentIsolation: true,
    prodTargetRejectedByDesign: true,
    spreadsheetId: ss.getId(),
    title: ss.getName(),
    timezone: ss.getSpreadsheetTimeZone(),
    requiredSheets: Object.keys(checked).length,
    contractVersion: EJS_CONFIG_V1.CONTRACT_VERSION
  };
}
