import pytest

from scripts.gd004.deployment_descriptor import select_deployment


DEPLOYMENT_ID = "AKfycbx0123456789abcdefghijklmnop"


def test_selects_nested_apps_script_api_shape_and_preserves_web_url():
    payload = {
        "deployments": [
            {
                "deploymentId": DEPLOYMENT_ID,
                "deploymentConfig": {"description": "EJS-GD004-ZERO-COST-TEST:abc123"},
                "entryPoints": [
                    {
                        "webApp": {
                            "url": f"https://script.google.com/macros/s/{DEPLOYMENT_ID}/exec"
                        }
                    }
                ],
            }
        ]
    }
    deployment_id, web_url = select_deployment(
        payload, description_prefix="EJS-GD004-ZERO-COST-TEST:abc123"
    )
    assert deployment_id == DEPLOYMENT_ID
    assert web_url.endswith(f"/{DEPLOYMENT_ID}/exec")


def test_selects_clasp_flat_shape_and_derives_web_url():
    payload = [
        {
            "deploymentId": DEPLOYMENT_ID,
            "description": "EJS-GD004-ZERO-COST-TEST:def456",
            "versionNumber": 7,
        }
    ]
    deployment_id, web_url = select_deployment(
        payload, description_prefix="EJS-GD004-ZERO-COST-TEST:def456"
    )
    assert deployment_id == DEPLOYMENT_ID
    assert web_url == f"https://script.google.com/macros/s/{DEPLOYMENT_ID}/exec"


def test_rejects_missing_exact_sha_deployment():
    with pytest.raises(ValueError, match="no deployment found"):
        select_deployment(
            [{"deploymentId": DEPLOYMENT_ID, "description": "other"}],
            description_prefix="EJS-GD004-ZERO-COST-TEST:missing",
        )
