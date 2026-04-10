"""Agent run pipeline control request schema."""

from director_api.api.schemas.agent_run import AgentRunPipelineControl


def test_pipeline_control_actions():
    assert AgentRunPipelineControl(action="pause").action == "pause"
    assert AgentRunPipelineControl(action="resume").action == "resume"
    assert AgentRunPipelineControl(action="stop").action == "stop"
