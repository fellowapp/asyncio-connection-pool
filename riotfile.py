from riot import Venv, latest

venv = Venv(
    pys=3,
    venvs=[
        Venv(
            pys=[3.8, 3.9],
            name="test",
            command="pytest {cmdargs}",
            pkgs={
                "pytest": "==6.1.2",
                "pytest-asyncio": "==0.14.0",
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
