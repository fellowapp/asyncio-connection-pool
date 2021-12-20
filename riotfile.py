from riot import Venv, latest

venv = Venv(
    pys=3,
    venvs=[
        Venv(
            pys=["3.8", "3.9", "3.10"],
            name="test",
            command="pytest {cmdargs}",
            pkgs={
                "pytest": "==6.2.5",
                "pytest-asyncio": "==0.16.0",
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
                "mypy": "==0.790",
            },
        ),
        Venv(
            pkgs={"black": "==20.8b1"},
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
            pkgs={"flake8": "==3.8.4"},
            command="flake8 test asyncio_connection_pool",
        ),
    ],
)
