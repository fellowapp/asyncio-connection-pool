from riot import Venv, latest

venv = Venv(
    pys=3,
    venvs=[
        Venv(
            pys=["3.8", "3.9", "3.10", "3.11", "3.12"],
            name="test",
            command="pytest {cmdargs}",
            pkgs={
                "pytest": latest,
                "pytest-asyncio": latest,
                "pytest-cov": latest,
                # extras_require
                "ddtrace": latest,
                "datadog": latest,
                "aioredis": latest,
            },
        ),
        Venv(
            name="mypy",
            command="mypy asyncio_connection_pool",
            pkgs={
                "mypy": "==1.1.1",
            },
        ),
        Venv(
            pkgs={"black": "==23.1.0"},
            venvs=[
                Venv(
                    name="fmt",
                    command=r"black --exclude '/\.riot/' .",
                ),
                Venv(
                    name="black",
                    command=r"black --exclude '/\.riot/' {cmdargs}",
                ),
            ],
        ),
        Venv(
            name="flake8",
            pkgs={"flake8": "==6.0.0"},
            command="flake8 test asyncio_connection_pool",
        ),
    ],
)
