import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--update-baseline-videos", action="store_true", help="Whether to save the videos generated by the tests as new baselines"
    )

@pytest.fixture(autouse=True)
def update_baseline_videos(request):
    return request.config.getoption("--update-baseline-videos")
