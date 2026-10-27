import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.test_workbench import _TEMP
from workbench.config import load_config
from workbench.agent_tools import write_json


class TeachingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=_TEMP)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def manager(self):
        self.assertIsNotNone(importlib.util.find_spec("workbench.teaching"), "teaching manager missing")
        from workbench.teaching import TeachingManager
        manager = TeachingManager(self.root, load_config)
        self.addCleanup(manager.close)
        return manager

    def test_credentials_are_not_in_public_settings_or_experiment(self):
        manager = self.manager()
        public = manager.configure({"api_key": "sk-unit-test-private", "remember_key": False,
                                    "model": "deepseek-v4-flash"})
        self.assertTrue(public["key_present"])
        self.assertNotIn("sk-unit-test-private", json.dumps(public))
        experiment = manager.create("测试计数器")
        self.assertNotIn("sk-unit-test-private", json.dumps(manager.get(experiment["id"])))
        self.assertNotIn("sk-unit-test-private", (self.root / "settings.json").read_text())
        self.assertEqual(manager.redact("error sk-unit-test-private!"), "error [REDACTED]!")

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_saved_key_can_be_made_memory_only_without_reentering_it(self):
        manager = self.manager()
        manager.configure({"api_key": "sk-unit-test-private", "remember_key": True})
        encrypted = self.root / "private/deepseek.dpapi"
        from workbench.teaching import protect
        self.assertNotIn(b"sk-unit-test-private", encrypted.read_bytes())
        self.assertEqual(protect(encrypted.read_bytes(), decrypt=True), b"sk-unit-test-private")
        result = manager.configure({"remember_key": False})
        self.assertFalse(result["key_saved"])
        self.assertTrue(result["key_present"])
        self.assertFalse(encrypted.exists())

    def test_state_survives_restart_and_running_turn_is_interrupted(self):
        manager = self.manager()
        experiment = manager.create("counter")
        path = manager.directory(experiment["id"]) / "session.json"
        saved = json.loads(path.read_text())
        saved["status"] = "running"
        saved["messages"] = [{"role": "user", "text": "make a counter"}]
        write_json(path, saved)
        from workbench.teaching import TeachingManager
        restarted = TeachingManager(self.root, load_config)
        self.addCleanup(restarted.close)
        result = restarted.get(experiment["id"])
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["messages"][0]["text"], "make a counter")
        self.assertFalse(result["simulation"]["verified_current"])

    def test_provider_configuration_allows_native_file_tools_in_the_experiment(self):
        manager = self.manager()
        experiment = manager.create("counter")
        from workbench.opencode import agent_config
        config = agent_config(manager.directory(experiment["id"]), "deepseek-v4-flash")
        self.assertEqual(config["permission"]["*"], "deny")
        self.assertEqual(config["permission"]["rtl_*"], "allow")
        for name in ('read', 'grep', 'glob', 'todowrite'):
            self.assertEqual(config['permission'][name], 'allow')
        self.assertEqual(config['permission']['edit']['*'], 'deny')
        self.assertEqual(config['permission']['edit'][str(manager.directory(experiment['id']) / 'source').replace('\\', '/') + '/*'], 'allow')
        self.assertEqual(config["enabled_providers"], ["deepseek"])
        self.assertNotIn("steps", config["agent"]["rtl_teacher"])
        self.assertNotIn("30 个 agent 步骤", config["agent"]["rtl_teacher"]["prompt"])
        self.assertNotIn("sk-", json.dumps(config))

    def test_turn_limits_default_unlimited_and_custom_values_persist(self):
        manager = self.manager()
        for field in ("max_agent_steps", "turn_timeout_minutes"):
            self.assertIsNone(manager.public_settings().get(field))
        self.assertIsNone(manager.public_settings()["limits"]["agent_steps"])
        self.assertIsNone(manager.public_settings()["limits"]["turn_timeout_s"])
        configured = manager.configure({"max_agent_steps": 60, "turn_timeout_minutes": 45})
        self.assertEqual(configured["limits"]["agent_steps"], 60)
        self.assertEqual(configured["limits"]["turn_timeout_s"], 2700)
        from workbench.teaching import TeachingManager
        restarted = TeachingManager(self.root, load_config)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.settings["max_agent_steps"], 60)
        self.assertEqual(restarted.settings["turn_timeout_minutes"], 45)
        for field in ("max_agent_steps", "turn_timeout_minutes"):
            for invalid in (0, -1, True, 1.5, "10"):
                with self.subTest(field=field, invalid=invalid), self.assertRaises(ValueError):
                    manager.configure({field: invalid})
        self.assertEqual(manager.settings["max_agent_steps"], 60)
        reset = manager.configure({"max_agent_steps": None, "turn_timeout_minutes": None})
        self.assertIsNone(reset["limits"]["agent_steps"])
        self.assertIsNone(reset["limits"]["turn_timeout_s"])

    def test_custom_steps_are_passed_to_opencode(self):
        from workbench.opencode import agent_config
        config = agent_config(self.root, max_agent_steps=60)
        self.assertEqual(config["agent"]["rtl_teacher"]["steps"], 60)

    def test_eda_limits_default_unlimited_and_can_be_configured_independently(self):
        manager = self.manager()
        fields = {'max_functional_runs': 'simulations', 'max_implementation_runs': 'implementations',
                  'max_post_route_runs': 'post_route_simulations'}
        for field, counter in fields.items():
            self.assertIsNone(manager.public_settings()['limits'][counter])
            self.assertIsNone(manager.settings[field])
        configured = manager.configure({'max_functional_runs': 8, 'max_implementation_runs': 6, 'max_post_route_runs': 4})
        self.assertEqual([configured[field] for field in fields], [8, 6, 4])
        self.assertEqual(manager.configure({'max_functional_runs': None})['limits']['implementations'], 6)
        for field in fields:
            with self.subTest(field=field), self.assertRaises(ValueError):
                manager.configure({field: 0})

    def test_send_snapshots_eda_limits_for_mcp_including_existing_experiments(self):
        manager = self.manager()
        identifier = manager.create()['id']
        manager.key = 'local-test-key'
        manager.configure({'max_functional_runs': 8, 'max_implementation_runs': 6, 'max_post_route_runs': 4})
        with patch('workbench.teaching.threading.Thread'):
            manager.send(identifier, 'continue existing experiment')
        from workbench.agent_tools import read_json
        control = read_json(manager.directory(identifier) / 'control.json')
        self.assertEqual(control['run_limits'], {'functional': 8, 'implementation': 6, 'post_route': 4, 'query': None})
        manager.jobs.clear()

    def test_provider_defaults_to_model_capacity_without_lowering_thinking(self):
        from workbench.opencode import MODEL_MAX_OUTPUT_TOKENS, agent_config
        model = agent_config(self.root)["provider"]["deepseek"]["models"]["deepseek-flash"]
        self.assertEqual(model["limit"]["output"], MODEL_MAX_OUTPUT_TOKENS)
        self.assertNotIn("reasoningEffort", model.get("options", {}))
        self.assertNotIn("thinking", model.get("options", {}))
        custom = agent_config(self.root, max_output_tokens=65536)["provider"]["deepseek"]["models"]["deepseek-flash"]
        self.assertEqual(custom["limit"]["output"], 65536)

    def test_output_cap_is_optional_validated_and_persisted(self):
        manager = self.manager()
        self.assertIsNone(manager.public_settings()["max_output_tokens"])
        manager.configure({"max_output_tokens": 65536})
        for invalid in (0, -1, True, 1.5, "65536", 393217):
            with self.subTest(value=invalid), self.assertRaisesRegex(ValueError, "输出上限"):
                manager.configure({"max_output_tokens": invalid})
        self.assertEqual(manager.settings["max_output_tokens"], 65536)
        from workbench.teaching import TeachingManager
        restarted = TeachingManager(self.root, load_config)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.public_settings()["max_output_tokens"], 65536)
        self.assertIsNone(manager.configure({"max_output_tokens": None})["max_output_tokens"])
        self.assertEqual(json.loads((self.root / "settings.json").read_text())["max_output_tokens"], None)

    def test_runtime_overrides_opencode_default_on_outbound_requests(self):
        from workbench.opencode import MODEL_MAX_OUTPUT_TOKENS, OpenCodeRuntime
        manager = self.manager()
        folder = manager.directory(manager.create()["id"])
        executable = self.root / "opencode.exe"
        executable.touch()
        for cap in (None, 65536):
            with self.subTest(cap=cap):
                runtime = OpenCodeRuntime(self.root / "runtime")
                process = Mock()
                process.poll.return_value = None
                with patch("workbench.opencode.subprocess.Popen", return_value=process) as launch, \
                     patch.object(runtime, "request", return_value={"healthy": True}):
                    runtime.start(folder, str(executable), "unit-test-key", "deepseek-flash", cap)
                self.assertEqual(launch.call_args.kwargs["env"]["OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX"],
                                 str(cap or MODEL_MAX_OUTPUT_TOKENS))
                runtime.output.close()

    def test_changed_tool_config_restarts_persistent_mcp(self):
        from workbench.opencode import OpenCodeRuntime
        folder = self.root / 'experiment'
        folder.mkdir()
        write_json(folder / 'tool-config.json', {'simulation_backend': 'modelsim'})
        runtime = OpenCodeRuntime(self.root / 'runtime')
        runtime.folder = folder.resolve()
        runtime.process = Mock()
        runtime.process.poll.return_value = None
        runtime.tool_config_digest = 'old-configuration'
        with patch.object(runtime, 'stop', side_effect=RuntimeError('restart requested')):
            with self.assertRaisesRegex(RuntimeError, 'restart requested'):
                runtime.start(folder, 'unused.exe', '', 'deepseek-flash')

    def test_rejects_bad_paths_and_unconfigured_turn_without_mutation(self):
        with patch.dict("os.environ", {}, clear=True):
            manager = self.manager()
            experiment = manager.create("counter")
            with self.assertRaises(ValueError):
                manager.directory("../outside")
            with self.assertRaisesRegex(ValueError, "API Key"):
                manager.send(experiment["id"], "make a counter")
            self.assertEqual(manager.get(experiment["id"])["messages"], [])

    def test_model_success_claim_cannot_set_verification_result(self):
        manager = self.manager()
        experiment = manager.create("counter")
        raw = [{"info": {"role": "assistant", "id": "a", "time": {"completed": 1},
                         "tokens": {"input": 10, "output": 20, "reasoning": 3}, "cost": 0.01},
                "parts": [{"type": "text", "text": "ALL TESTS PASSED"}]}]
        manager.store_messages(experiment["id"], raw)
        result = manager.get(experiment["id"])
        self.assertFalse(result["simulation"]["verified_current"])
        self.assertEqual(result["messages"][-1]["text"], "ALL TESTS PASSED")
        self.assertEqual(result["usage"]["input"], 10)

    def running_experiment(self, manager):
        experiment = manager.create("counter")
        manager._update(experiment["id"], status="running")
        from workbench.teaching_jobs import ExperimentJob
        manager.runtime = Mock()  # Test handle for this one runtime.
        manager.runtimes[experiment["id"]] = manager.runtime
        job = ExperimentJob(manager.runtime, dict(manager.settings), manager.key)
        manager.jobs[experiment["id"]] = job
        manager.cancelled = job.cancelled
        return experiment["id"]

    @staticmethod
    def assistant(identifier, finish="stop", text="", completed=True, reasoning=0):
        return {"info": {"id": identifier, "role": "assistant", "finish": finish,
                         "time": {"completed": 1} if completed else {},
                         "tokens": {"reasoning": reasoning}},
                "parts": [{"type": "text", "text": text}] if text else [
                    {"type": "reasoning", "text": "private reasoning must not be exposed"}]}

    def test_reasoning_activity_and_finish_are_visible_without_reasoning_content(self):
        manager = self.manager()
        identifier = manager.create()["id"]
        raw = [self.assistant("a", finish=None, completed=False)]
        manager.store_messages(identifier, raw)
        message = manager.get(identifier)["messages"][-1]
        self.assertEqual(message["activity"], "reasoning")
        self.assertFalse(message["completed"])
        self.assertNotIn("private reasoning", json.dumps(message))
        raw = [self.assistant("a", finish="length", reasoning=32000)]
        manager.store_messages(identifier, raw)
        message = manager.get(identifier)["messages"][-1]
        self.assertEqual(message["finish"], "length")
        self.assertTrue(message["completed"])
        self.assertEqual(manager.get(identifier)["usage"]["reasoning"], 32000)

    def test_length_cutoff_continues_same_session_once_and_keeps_tool_budget(self):
        manager = self.manager()
        identifier = self.running_experiment(manager)
        cutoff = [self.assistant("a", finish="length", reasoning=32000)]
        complete = cutoff + [self.assistant("b", text="已保存设计，请查看实际仿真结果。")]
        manager.runtime.request.side_effect = [
            {"id": "ses_test"}, [], None, cutoff, {}, None, complete, {}]
        with patch.object(manager.cancelled, "wait", return_value=False), \
             patch("workbench.teaching.ExperimentTools.begin_turn") as begin_turn:
            manager._run(identifier, "generate counter")
        prompts = [call for call in manager.runtime.request.call_args_list
                   if call.args[1].endswith("/prompt_async")]
        self.assertEqual(len(prompts), 2)
        self.assertEqual(prompts[0].args[1], prompts[1].args[1])
        self.assertNotIn("variant", prompts[1].args[2])
        self.assertTrue(prompts[1].args[2]["parts"][0]["synthetic"])
        self.assertEqual(manager.get(identifier)["status"], "ready")
        self.assertEqual(manager.get(identifier)["continuation"]["attempt"], 1)
        begin_turn.assert_not_called()
        manager.runtime.stop.assert_called_once()
        self.assertFalse(manager.busy)

    def test_repeated_empty_or_truncated_reply_fails_instead_of_silent_ready(self):
        for finish in ("length", "stop", "tool-calls"):
            with self.subTest(finish=finish):
                manager = self.manager()
                manager.settings['max_continuations'] = 1
                identifier = self.running_experiment(manager)
                first = [self.assistant("a", finish=finish)]
                second = first + [self.assistant("b", finish=finish)]
                manager.runtime.request.side_effect = [
                    {"id": "ses_test"}, [], None, first, {}, None, second, {}]
                with patch.object(manager.cancelled, "wait", return_value=False):
                    manager._run(identifier, "generate counter")
                result = manager.get(identifier)
                self.assertEqual(result["status"], "failed")
                self.assertIn("自动续跑", result["error"])
                self.assertIn("1", result["error"])
                manager.runtime.stop.assert_called_once()
                self.assertFalse(manager.busy)

    def test_multiple_cutoffs_continue_by_default_and_cap_is_optional(self):
        manager = self.manager()
        self.assertIsNone(manager.settings['max_continuations'])
        identifier = self.running_experiment(manager)
        history = []
        responses = [{'id': 'ses_test'}, [], None]
        for i in range(3):
            history = history + [self.assistant(str(i), finish='length')]
            responses += [history, {}, None]
        responses += [history + [self.assistant('done', text='Verified completion')], {}]
        manager.runtime.request.side_effect = responses
        with patch.object(manager.cancelled, 'wait', return_value=False):
            manager._run(identifier, 'continue')
        self.assertEqual(manager.get(identifier)['status'], 'ready')
        self.assertEqual(manager.get(identifier)['continuation']['attempt'], 3)
        manager.configure({'max_continuations': 2, 'max_message_chars': 100, 'max_query_runs': 9})
        self.assertEqual(manager.public_settings()['limits']['automatic_continuations'], 2)
        with self.assertRaisesRegex(ValueError, '100'):
            manager.send(identifier, 'x' * 101)
        manager.configure({'max_continuations': None, 'max_message_chars': None, 'max_query_runs': None})
        manager.key = 'fixture'
        with patch('workbench.teaching.threading.Thread'):
            manager.send(identifier, 'long requirement ' * 2000)
        self.assertGreater(len(manager.get(identifier)['messages'][-1]['text']), 16000)

    def test_native_edit_is_recorded_and_invalidates_previous_source_hash(self):
        manager = self.manager()
        identifier = manager.create()['id']
        manager.source_snapshots[identifier] = {}
        path = manager.directory(identifier) / 'source/rtl/native.v'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('module native; endmodule', encoding='utf-8')
        raw = [self.assistant('a', text='saved')]
        raw[0]['parts'].append({'id': 'native-edit-1', 'type': 'tool', 'tool': 'write',
                               'state': {'status': 'completed', 'input': {'filePath': str(path)}, 'output': 'saved'}})
        manager.store_messages(identifier, raw)
        manager.store_messages(identifier, raw)
        changes = [e for e in manager.get(identifier)['events'] if e['type'] == 'file_changed']
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['editor'], 'opencode')
        self.assertIn('+module native', changes[0]['diff'])

    def test_normal_answer_does_not_trigger_continuation(self):
        manager = self.manager()
        identifier = self.running_experiment(manager)
        manager.runtime.request.side_effect = [
            {"id": "ses_test"}, [], None, [self.assistant("a", text="这是组合逻辑。")], {}]
        with patch.object(manager.cancelled, "wait", return_value=False):
            manager._run(identifier, "explain")
        self.assertEqual(manager.get(identifier)["status"], "ready")
        self.assertEqual(sum(call.args[1].endswith("/prompt_async")
                             for call in manager.runtime.request.call_args_list), 1)

    def test_continuation_waits_for_a_new_assistant_message(self):
        manager = self.manager()
        identifier = self.running_experiment(manager)
        old = [self.assistant("a", finish="length")]
        final = old + [self.assistant("b", text="继续完成了。")]
        manager.runtime.request.side_effect = [
            {"id": "ses_test"}, [], None, old, {}, None, old, {}, final, {}]
        with patch.object(manager.cancelled, "wait", return_value=False):
            manager._run(identifier, "generate counter")
        self.assertEqual(manager.get(identifier)["status"], "ready")
        self.assertEqual(sum(call.args[1].endswith("/prompt_async")
                             for call in manager.runtime.request.call_args_list), 2)

    def test_continuation_does_not_extend_the_turn_deadline(self):
        manager = self.manager()
        manager.settings["turn_timeout_minutes"] = 10
        identifier = self.running_experiment(manager)
        cutoff = [self.assistant("a", finish="length")]
        manager.runtime.request.side_effect = [
            {"id": "ses_test"}, [], None, cutoff, {}, None]
        # Start at zero, retry just before 600 seconds, then reach the original deadline.
        with patch.object(manager.cancelled, "wait", return_value=False), \
             patch("workbench.teaching.time.monotonic", side_effect=[0, 599, 599, 601]):
            manager._run(identifier, "generate counter")
        self.assertEqual(manager.get(identifier)["status"], "timeout")
        manager.runtime.stop.assert_called_once()

    def test_step_budget_exhaustion_prevents_automatic_continuation(self):
        manager = self.manager()
        manager.settings["max_agent_steps"] = 30
        identifier = self.running_experiment(manager)
        exhausted = [self.assistant(str(i), finish="length") for i in range(30)]
        manager.runtime.request.side_effect = [
            {"id": "ses_test"}, [], None, exhausted, {}]
        with patch.object(manager.cancelled, "wait", return_value=False):
            manager._run(identifier, "generate counter")
        self.assertIn("30", manager.get(identifier)["error"])
        self.assertEqual(sum(call.args[1].endswith("/prompt_async")
                             for call in manager.runtime.request.call_args_list), 1)

    def test_unlimited_turn_can_exceed_thirty_steps_and_ten_minutes(self):
        manager = self.manager()
        identifier = self.running_experiment(manager)
        completed = [self.assistant(str(i), text="已完成下一步。") for i in range(45)]
        manager.runtime.request.side_effect = [{"id": "ses_test"}, [], None, completed, {}]
        with patch.object(manager.cancelled, "wait", return_value=False), \
             patch("workbench.teaching.time.monotonic", side_effect=[0, 3601, 3602, 3603]):
            manager._run(identifier, "generate design")
        self.assertEqual(manager.get(identifier)["status"], "ready")
        self.assertFalse(manager.busy)
        manager.runtime.stop.assert_called_once()

    def test_custom_deadline_stops_running_turn(self):
        manager = self.manager()
        manager.settings["turn_timeout_minutes"] = 2
        identifier = self.running_experiment(manager)
        manager.runtime.request.side_effect = [{"id": "ses_test"}, [], None]
        with patch.object(manager.cancelled, "wait", return_value=False), \
             patch("workbench.teaching.time.monotonic", side_effect=[0, 121]):
            manager._run(identifier, "generate design")
        self.assertEqual(manager.get(identifier)["status"], "timeout")
        self.assertIn("2 分钟", manager.get(identifier)["error"])
        manager.runtime.stop.assert_called_once()

    def test_custom_step_cap_stops_agent_exceeding_budget(self):
        manager = self.manager()
        manager.settings["max_agent_steps"] = 2
        identifier = self.running_experiment(manager)
        messages = [self.assistant(str(i), text="正在执行。") for i in range(3)]
        manager.runtime.request.side_effect = [{"id": "ses_test"}, [], None, messages, {}]
        with patch.object(manager.cancelled, "wait", return_value=False):
            manager._run(identifier, "generate design")
        self.assertEqual(manager.get(identifier)["status"], "failed")
        self.assertIn("2 个助手步骤", manager.get(identifier)["error"])
        manager.runtime.stop.assert_called_once()

    def test_unlimited_turn_still_obeys_manual_stop(self):
        manager = self.manager()
        identifier = self.running_experiment(manager)
        manager.runtime.request.side_effect = [{"id": "ses_test"}, [], None]
        with patch.object(manager.cancelled, "wait", return_value=True):
            manager._run(identifier, "generate design")
        self.assertEqual(manager.get(identifier)["status"], "cancelled")
        manager.runtime.stop.assert_called_once()
        self.assertFalse(manager.busy)

    def test_automatic_prompt_is_distinguished_from_student_input(self):
        manager = self.manager()
        identifier = manager.create()["id"]
        manager.store_messages(identifier, [{"info": {"id": "u", "role": "user"},
            "parts": [{"type": "text", "text": "平台自动续跑", "synthetic": True}]}])
        self.assertEqual(manager.get(identifier)["messages"][-1]["origin"], "platform")

    def test_transport_failure_stops_old_agent_before_releasing_turn(self):
        manager = self.manager()
        identifier = self.running_experiment(manager)
        manager.runtime.request.side_effect = [{"id": "ses_test"}, [], None, OSError("connection lost")]
        manager.runtime.stop.side_effect = lambda: self.assertTrue(manager.busy)
        manager._run(identifier, "generate counter")
        manager.runtime.stop.assert_called_once()
        self.assertTrue((manager.directory(identifier) / "cancel.flag").exists())
        self.assertEqual(manager.get(identifier)["status"], "failed")
        self.assertFalse(manager.busy)
        manager.runtime.stop.side_effect = None

    def test_stop_during_startup_finishes_cancelled_and_stops_runtime(self):
        manager = self.manager()
        identifier = self.running_experiment(manager)
        manager.runtime.start.side_effect = lambda *args: manager.cancelled.set()
        manager._run(identifier, "generate counter")
        manager.runtime.stop.assert_called_once()
        self.assertEqual(manager.get(identifier)["status"], "cancelled")
        self.assertFalse(manager.busy)

    def test_failed_cleanup_blocks_new_turn_until_stop_retry_succeeds(self):
        manager = self.manager()
        identifier = self.running_experiment(manager)
        manager.runtime.start.side_effect = OSError("startup failed")
        manager.runtime.stop.side_effect = [OSError("cleanup failed"), None]
        manager._run(identifier, "generate counter")
        self.assertTrue(manager.busy)
        with self.assertRaisesRegex(ValueError, "运行中"):
            manager.send(identifier, "try again")
        with self.assertRaisesRegex(ValueError, "停止"):
            manager.configure({})
        manager.stop(identifier)
        self.assertFalse(manager.busy)
        self.assertEqual(manager.get(identifier)["status"], "cancelled")
        manager.runtime.stop.side_effect = None

    def test_stop_during_history_read_does_not_submit_prompt(self):
        manager = self.manager()
        identifier = self.running_experiment(manager)
        def request(method, path, body=None):
            if path == '/session': return {'id':'ses_test'}
            if path.endswith('/message'):
                manager.cancelled.set()
                return []
            self.fail('Cancelled job submitted request: '+path)
        manager.runtime.request.side_effect = request
        manager._run(identifier, 'do not submit after cancellation')
        self.assertEqual(manager.get(identifier)['status'], 'cancelled')
        manager.runtime.stop.assert_called_once()

    def test_source_edit_invalidates_passed_session_and_history_status(self):
        manager = self.manager()
        identifier = manager.create("counter")["id"]
        folder = manager.directory(identifier)
        from workbench.agent_tools import ExperimentTools
        tools = ExperimentTools(folder)
        tools.call("write_file", {"path": "rtl/dut.sv", "content": "module dut; endmodule"})
        write_json(folder / "evidence/test/result.json", {
            "id": "test", "time": "2026-10-23T00:00:00", "status": "passed", "ok": True,
            "source_sha256": tools.digest(tools.files())})
        manager._update(identifier, status="passed")
        self.assertEqual(manager.get(identifier)["status"], "passed")
        tools.call("write_file", {"path": "rtl/dut.sv", "content": "module dut; wire change; endmodule"})
        self.assertEqual(manager.get(identifier)["status"], "needs_test")
        self.assertEqual(manager.history()[0]["status"], "needs_test")


if __name__ == "__main__":
    unittest.main()
