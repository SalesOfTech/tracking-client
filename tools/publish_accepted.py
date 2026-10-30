"""Resume only publication of a completed, fully successful native acceptance."""
import json
import os
import re
import subprocess
import sys

import accept_release as gate


def validate_record(api, run, plan):
    gate.require(run.get('status') == 'completed' and run.get('conclusion') in ('success', 'failure'))
    gate.require(run.get('event') == 'workflow_dispatch' and run.get('head_branch') == 'main'
                 and run.get('path') == '.github/workflows/accept-release.yml'
                 and run.get('head_repository', {}).get('full_name') == api.repo)
    gate.require(plan['acceptance_run_id'] == run['id'] and plan['acceptance_run_attempt'] == run['run_attempt']
                 and plan['harness_sha'] == run['head_sha'] and plan['repository'] == api.repo
                 and plan['version'] == gate.VERSION
                 and plan['dispatch_actor'] == run['actor']['login']
                 and plan['triggering_actor'] == run['triggering_actor']['login'])
    gate.require(plan['trusted_keys'] == gate.trusted_keys(os.environ['TRACKING_TRUSTED_UPDATE_KEYS']))
    comparison = api.api('compare/' + run['head_sha'] + '...' + os.environ['GITHUB_SHA'])
    gate.require(comparison.get('status') in ('identical', 'ahead'))
    jobs = api.jobs(run['id'], run['run_attempt'])
    expected = {'prepare', 'promote'} | {'accept-' + row['target'] for row in gate.TARGETS}
    if '3.5.0' in gate.upgrade_baselines(gate.VERSION):
        expected |= {'verify-macos-15-arm64', 'verify-macos-26-arm64'}
    gate.require(len(jobs) == len(expected) and {job['name'] for job in jobs} == expected)
    gate.require(all(job['status'] == 'completed' and job['conclusion'] == 'success'
                     for job in jobs if job['name'] != 'promote'))
    gate.accepted_jobs(api, plan)
    gate.revalidate(api, plan)


def main():
    try:
        gate.hosted_context('publish-accepted.yml')
        if len(sys.argv) == 2 and sys.argv[1] == 'cleanup':
            gate.cleanup()
            return 0
        gate.require(len(sys.argv) == 1)
        api = gate.GitHub()
        gate.authorize_dispatch(api, 'publish-accepted.yml')
        identifier = os.environ['ACCEPTED_RUN_ID']
        gate.require(re.fullmatch(r'[1-9][0-9]{1,19}', identifier))
        gate.STAGE = 'completed-acceptance-record'
        run = api.api('actions/runs/' + identifier)
        gate.require(run.get('status') == 'completed')
        jobs = api.jobs(run['id'], run['run_attempt'])
        promotion = [job for job in jobs if job['name'] == 'promote']
        gate.require(len(promotion) == 1 and promotion[0]['status'] == 'completed')
        log = subprocess.check_output(['gh', 'api', '--allow-escape-sequences',
                                      'repos/' + api.repo + '/actions/jobs/' + str(promotion[0]['id']) + '/logs'],
                                     stderr=subprocess.PIPE, timeout=90)
        gate.require(len(log) <= 16 * 1024 * 1024)
        records = {line.split('ACCEPTANCE_PLAN: ', 1)[1].strip() for line in log.decode().splitlines()
                   if 'ACCEPTANCE_PLAN: {' in line}
        gate.require(len(records) == 1)
        plan = gate.json_value(records.pop())
        validate_record(api, run, plan)
        gate.publish_verified(api, plan, allow_published=True)
        print('PASS: published exact accepted assets; no build or native tests repeated')
        return 0
    except Exception:
        print('FAIL: resume publication stage=' + gate.STAGE)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
