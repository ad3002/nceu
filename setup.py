from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="nceu",
    version="0.1.0",
    author="Aleksey Komissarov",
    author_email="ad3002@gmail.com",
    description="A ncurses-based Gmail inbox analyzer and manager",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/ad3002/nceu",
    packages=find_packages(),
    install_requires=[
        "google-auth-oauthlib",
        "google-auth-httplib2",
        "google-api-python-client",
        "python-dateutil"
    ],
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
    ],
    python_requires=">=3.6",
    entry_points={
        "console_scripts": [
            "nceu=nceu.main:main",
        ],
    },
)
