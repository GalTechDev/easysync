from setuptools import setup, find_packages

setup(
    name="easysync",
    version="0.1.0",
    author="GalTechDev",
    description="Synchronisation universelle d'états Python en temps réel sur le réseau.",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/GalTechDev/easysync",
    packages=find_packages(),
    python_requires=">=3.8",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Topic :: System :: Networking",
        "Topic :: Software Development :: Libraries",
    ],
)
