from setuptools import setup, find_packages

with open("README.md", "r") as f:
    long_description = f.read()

setup(
    name="asyncio-connection-pool",
    description="A high-throughput, optionally-burstable pool free of explicit locking",
    url="https://github.com/fellowinsights/asyncio-connection-pool",
    author="Patrick Gingras <775.pg.12@gmail.com>",
    author_email="775.pg.12@gmail.com",
    classifiers=[
        "Programming Language :: Python",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "License :: OSI Approved :: BSD License",
    ],
    keywords="asyncio",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_packages(
        include=["asyncio_connection_pool", "asyncio_connection_pool.*"]
    ),
    package_data={"asyncio_connection_pool": ["py.typed"]},
    python_requires=">=3.8",
    install_requires=[],
    tests_require=["riot"],
    extras_require={
        "datadog": ["ddtrace", "datadog"],
        "aioredis": ["aioredis"],
    },
    setup_requires=["setuptools_scm"],
    use_scm_version=True,
    zip_safe=False,  # for mypy support
)
