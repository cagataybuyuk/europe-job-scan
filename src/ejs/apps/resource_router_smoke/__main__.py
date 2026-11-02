"""Run with python -m ejs.apps.resource_router_smoke; no runtime integration."""
import argparse
from dataclasses import asdict
import json

from ejs.services.resource_router import Provider, Request, quota_band, route


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', required=True, choices=('TEST',))
    parser.add_argument('--approval-ref', required=True, help='Opaque TEST review reference; no credentials')
    parser.add_argument('--source-sha', required=True, help='Exact source commit, manually supplied')
    parser.add_argument('--remaining-percent', type=int, default=0, choices=range(101), metavar='0..100')
    parser.add_argument('--scenario', choices=('ci_available', 'local_only', 'no_provider'), default='no_provider')
    args = parser.parse_args(argv)
    # These are fixture declarations, never discovered availability or real clients.
    providers = (
        Provider('github_ci', args.scenario == 'ci_available', {'python_tests'}, True),
        Provider('local_macbook', args.scenario != 'no_provider', {'python_tests'}),
    )
    request = Request(
        task_id='synthetic-gd005-cli', environment=args.environment,
        approval_ref=args.approval_ref, source_sha=args.source_sha,
        risk_level='low', required_capabilities={'python_tests'},
        quota_band=quota_band(args.remaining_percent), network_requirement='offline',
        test_evidence=('not_run',),
    )
    decision = route(request, providers)
    print(json.dumps({'mode': 'synthetic_advisory_only', 'environment': 'TEST',
                      'scenario': args.scenario, 'quota_band': request.quota_band,
                      'source_sha': args.source_sha if decision.stop_reason is None else None,
                      'provider_executed': False, 'test_evidence': ['not_run'],
                      'decision': asdict(decision)}, sort_keys=True))
    return 2 if decision.stop_reason else 0


if __name__ == '__main__':
    raise SystemExit(main())
