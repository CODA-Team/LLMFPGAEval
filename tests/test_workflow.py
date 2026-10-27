"""Each stage retains its own latest attempted result independently of edits."""
import unittest
from copy import deepcopy


def experiment():
    result = {'id': 'f1', 'source_sha256': 'source', 'project_sha256': 'project',
              'ok': False, 'status': 'failed', 'inputs_current': True, 'evidence_valid': True}
    return {'status': 'running', 'files': [{'path': 'rtl/top.v'}, {'path': 'tb/tb.sv'}],
            'messages': [], 'simulation': {'source_sha256': 'source', 'verified_current': False, 'latest': result},
            'flow': {'current_project_sha256': 'project', 'project': None,
                     'implementation_ok': False, 'post_route_ok': False}}


class WorkflowTests(unittest.TestCase):
    def states(self, value):
        from workbench.workflow import stage_states
        return stage_states(value)

    def test_simulation_already_ran_without_project_registration(self):
        state = experiment()
        self.assertEqual(self.states(state), ['prepared', 'failed'] + ['pending'] * 5)
        state['flow']['project'] = {'top': 'top'}
        self.assertEqual(self.states(state)[0], 'passed')

    def test_repair_retains_downstream_and_does_not_modify_history(self):
        state = experiment()
        state['flow']['implementation'] = deepcopy(state['simulation']['latest'])
        old = deepcopy(state)
        state['simulation']['source_sha256'] = 'new-testbench'
        self.assertEqual(self.states(state), ['prepared', 'failed', 'failed'] + ['pending'] * 4)
        self.assertEqual(state['simulation']['latest'], old['simulation']['latest'])
        state['status'] = 'ready'
        self.assertEqual(self.states(state), ['prepared', 'failed', 'failed'] + ['pending'] * 4)

    def test_actual_write_keeps_functional_result(self):
        state = experiment()
        state['messages'] = [{'tools': [{'name': 'rtl_write_file', 'status': 'running'}]}]
        self.assertEqual(self.states(state), ['running', 'failed'] + ['pending'] * 5)
        state['messages'][0]['tools'][0]['status'] = 'error'
        self.assertEqual(self.states(state)[1], 'failed')

    def test_rerun_and_live_native_td_steps(self):
        state = experiment()
        state['flow']['project'] = {'top': 'top'}
        state['simulation']['latest']['status'] = 'running'
        self.assertEqual(self.states(state), ['passed', 'running'] + ['pending'] * 5)
        state['simulation']['latest'].update(ok=True, status='passed')
        state['simulation']['verified_current'] = True
        state['flow']['implementation'] = {**state['simulation']['latest'], 'ok': False, 'status': 'running',
            'steps': [{'name': name, 'status': 'passed'} for name in ('optimize_rtl', 'optimize_gate', 'place')]
                     + [{'name': 'route', 'status': 'running'}]}
        self.assertEqual(self.states(state), ['passed', 'passed', 'passed', 'passed', 'running', 'pending', 'pending'])
        state['flow']['implementation']['status'] = 'failed'
        self.assertEqual(self.states(state)[4], 'failed')

    def test_failed_functional_rerun_keeps_old_build_result(self):
        state = experiment()
        state['flow']['implementation'] = {**state['simulation']['latest'], 'ok': True, 'status': 'passed'}
        self.assertEqual(self.states(state)[2:], ['passed'] * 4 + ['pending'])

    def test_invalid_evidence_and_cancelled_runs_are_not_green_or_running(self):
        state = experiment()
        state['simulation']['latest'].update(ok=True, status='passed')
        state['simulation']['latest']['evidence_valid'] = False
        self.assertEqual(self.states(state)[1], 'stale')
        state['status'] = 'cancelled'
        state['simulation']['latest'].update(ok=False, status='running')
        self.assertEqual(self.states(state)[1], 'cancelled')

    def test_build_evidence_and_project_change_invalidation(self):
        state = experiment()
        state['simulation']['latest'].update(ok=True, status='passed')
        state['simulation']['verified_current'] = True
        state['flow']['implementation'] = deepcopy(state['simulation']['latest'])
        state['flow']['implementation']['evidence_valid'] = False
        self.assertEqual(self.states(state)[2:6], ['stale'] * 4)
        state['flow']['current_project_sha256'] = 'new-project'
        self.assertEqual(self.states(state), ['prepared', 'passed'] + ['stale'] * 4 + ['pending'])

    def test_previous_cancelled_turn_does_not_reopen_editing(self):
        state = experiment()
        state['messages'] = [{'tools': [{'name': 'rtl_write_file', 'status': 'running'}]},
                             {'role': 'user', 'text': 'explain the failure'}]
        self.assertEqual(self.states(state)[1], 'failed')

    def test_routing_completion_does_not_hide_failed_timing(self):
        state = experiment()
        state['flow']['project'] = {'top': 'top'}
        state['simulation']['latest'].update(ok=True, status='passed')
        state['simulation']['verified_current'] = True
        state['flow']['implementation'] = {**state['simulation']['latest'], 'timing_ok': False}
        state['flow']['implementation_ok'] = True
        self.assertEqual(self.states(state), ['passed', 'passed', 'passed', 'passed', 'failed', 'passed', 'pending'])


    def test_partial_td_retry_keeps_later_stages_and_their_run(self):
        from workbench.workflow import stage_results
        state = experiment()
        previous = {**state['simulation']['latest'], 'id': 'build1', 'ok': True, 'status': 'passed'}
        latest = {**previous, 'id': 'build2', 'ok': False, 'status': 'running',
                  'steps': [{'name': 'optimize_rtl', 'status': 'running'}]}
        state['flow'].update(implementation=latest, runs={'implementation': [previous, latest]},
                             post_route={**previous, 'id': 'post1', 'implementation_id': 'build1'})
        self.assertEqual(self.states(state)[2:], ['running'] + ['passed'] * 4)
        rows = stage_results(state)
        self.assertEqual(rows[2]['run']['id'], 'build2')
        self.assertEqual(rows[3]['run']['id'], 'build1')
        self.assertEqual(rows[5]['run']['id'], 'build1')
        self.assertEqual(rows[6]['run']['id'], 'post1')
        latest['status'] = 'failed'
        self.assertEqual(self.states(state)[2:], ['failed'] + ['passed'] * 4)

    def test_functional_retry_and_project_edit_leave_downstream_visible(self):
        state = experiment()
        run = {**state['simulation']['latest'], 'ok': True, 'status': 'passed'}
        state['flow'].update(implementation=deepcopy(run), post_route=deepcopy(run), project={'top': 'top'})
        state['flow']['current_project_sha256'] = 'new-project'
        state['simulation']['source_sha256'] = 'new-testbench'
        state['simulation']['latest']['status'] = 'running'
        self.assertEqual(self.states(state), ['passed', 'running'] + ['passed'] * 5)
        state['simulation']['latest']['status'] = 'failed'
        self.assertEqual(self.states(state), ['passed', 'failed'] + ['passed'] * 5)

    def test_timing_violation_remains_failed_when_later_bitgen_also_fails(self):
        state = experiment()
        state['flow']['implementation'] = {
            'id': 'timing-failed-build', 'ok': False, 'status': 'failed', 'timing_ok': False,
            'steps': [{'name': name, 'status': 'passed'} for name in
                      ('optimize_rtl', 'optimize_gate', 'place', 'route', 'report_timing', 'report_clock_summary')]
                     + [{'name': 'bitgen', 'status': 'failed'}]}
        self.assertEqual(self.states(state)[2:6], ['passed', 'passed', 'failed', 'failed'])

    def test_cleaned_partial_run_marks_only_reached_passes_as_unverified(self):
        state = experiment()
        previous = {**state['simulation']['latest'], 'id': 'old', 'ok': True, 'status': 'passed'}
        retry = {'id': 'partial', 'ok': False, 'status': 'failed', 'evidence_valid': False,
                 'steps': [{'name': name, 'status': 'passed'} for name in ('optimize_rtl', 'optimize_gate', 'place')]
                          + [{'name': 'route', 'status': 'failed'}]}
        state['flow'].update(implementation=retry, runs={'implementation': [previous, retry]})
        self.assertEqual(self.states(state)[2:6], ['stale', 'stale', 'failed', 'passed'])
        retry['status'] = 'running'
        retry['steps'][-1]['status'] = 'running'
        self.assertEqual(self.states(state)[2:6], ['passed', 'passed', 'running', 'passed'])

    def test_old_fixed_template_failure_without_journal_is_not_synthesis_only(self):
        state = experiment()
        previous = {**state['simulation']['latest'], 'id': 'old', 'ok': True, 'status': 'passed'}
        retry = {'id': 'old-unknown', 'executor': 'fixed_template', 'ok': False, 'status': 'failed'}
        state['flow'].update(implementation=retry, runs={'implementation': [previous, retry]})
        self.assertEqual(self.states(state)[2:6], ['unknown'] * 4)


if __name__ == '__main__':
    unittest.main()
