#!/usr/bin/env python3
import os
import glob
import subprocess
import zipfile
import boto3
import click
import yaml
import attr
import typing


@attr.s(auto_attribs=True)
class Function:
    name: str
    description: str
    runtime: str
    role_arn: str
    handler: str
    files: typing.List[str]
    layers: typing.List[str]
    environment: typing.Dict[str, str]


def parse_environment_spec(value):
    if isinstance(value, str):
        return value
    elif isinstance(value, dict):
        if "paramstore" in value:
            ssm = boto3.client("ssm")
            resp = ssm.get_parameter(Name=value["paramstore"], WithDecryption=True)
            return resp["Parameter"]["Value"]
    raise ValueError(value)


def create_psycopg2_layer():
    if not os.path.exists("awslambda-psycopg2"):
        subprocess.run(
            ["git", "clone", "git@github.com:jkehler/awslambda-psycopg2.git"],
            check=True,
        )
    prefix = "awslambda-psycopg2/psycopg2-3.7/"
    with zipfile.ZipFile("psycopg2.zip", "w") as lz:
        for file in glob.glob(prefix + "*"):
            arcname = file.replace(prefix, "python/psycopg2/")
            lz.write(file, arcname)

    # upload
    client = boto3.client("lambda")
    client.publish_layer_version(
        LayerName="py37-psycopg2",
        Description="python 3.7 psycopg2 layer",
        Content={"ZipFile": open("psycopg2.zip", "rb").read()},
        CompatibleRuntimes=["python3.7"],
    )


def do_publish(function):
    zipfilename = "upload.zip"
    with zipfile.ZipFile(zipfilename, "w") as zf:
        for fileglob in function.files:
            for fn in glob.glob(fileglob):
                zf.write(fn)
                click.echo(f"adding {fn}")

    client = boto3.client("lambda")

    layer_arns = []
    for layer in function.layers:
        versions = client.list_layer_versions(LayerName=layer)
        # TODO: is zero the right index?
        layer_arn = versions["LayerVersions"][0]["LayerVersionArn"]
        layer_arns.append(layer_arn)

    try:
        existing_config = client.get_function_configuration(FunctionName=function.name)
    except client.exceptions.ResourceNotFoundException:
        existing_config = False
        client.create_function(
            FunctionName=function.name,
            Runtime=function.runtime,
            Role=function.role_arn,
            Handler=function.handler,
            Code={"ZipFile": open(zipfilename, "rb").read()},
            Description=function.description,
            Environment={"Variables": function.environment},
            Publish=True,
            Layers=layer_arns,
        )
        print(f"created function {function.name}")

    if existing_config:
        client.update_function_code(
            FunctionName=function.name, ZipFile=open(zipfilename, "rb").read()
        )
        client.update_function_configuration(
            FunctionName=function.name,
            Role=function.role_arn,
            Handler=function.handler,
            Description=function.description,
            Environment={"Variables": function.environment},
        )
        client.publish_version(FunctionName=function.name)
        print(f"updated function {function.name}")


functions = {}


@click.group()
def cli():
    filename = "tripod.yaml"
    with open(filename) as f:
        data = yaml.safe_load(f)
    for function in data["functions"]:
        env = function.pop("environment")
        processed_env = {}
        for k, v in env.items():
            processed_env[k] = parse_environment_spec(v)
        functions[function["name"]] = Function(**function, environment=processed_env)


@cli.command()
def list():
    click.echo("available functions: ")
    for function in functions.values():
        click.echo(f"  {function.name}")


@cli.command()
@click.argument("function")
def publish(function):
    click.echo(f"publishing {function}")
    do_publish(functions[function])


if __name__ == "__main__":
    cli()
