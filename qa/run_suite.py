"""Run independent QA in dependency order. No model/network/container execution."""
import unittest
modules=['test_artifact_acceptance','test_schema_acceptance','test_runtime_regression','test_sandbox_acceptance','test_sources_acceptance','test_source_selection_acceptance','test_workflow_acceptance','test_campaign_acceptance','test_full_protocol_acceptance','test_export_trace_acceptance','test_actual_tokenizer_acceptance']
suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in modules)
result=unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
