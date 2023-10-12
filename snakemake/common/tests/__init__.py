from abc import ABC, abstractmethod
from pathlib import Path
import shutil
from typing import List, Mapping, Optional

import pytest
from snakemake import api, settings

from snakemake_interface_common.plugin_registry.plugin import TaggedSettings
from snakemake_interface_executor_plugins.settings import ExecutorSettingsBase
from snakemake_interface_executor_plugins.registry import ExecutorPluginRegistry
from snakemake_interface_storage_plugins.settings import StorageProviderSettingsBase


def handle_testcase(func):
    def wrapper(self, tmp_path):
        if self.expect_exception is None:
            try:
                return func(self, tmp_path)
            finally:
                self.cleanup_test()
        else:
            with pytest.raises(self.expect_exception):
                try:
                    return func(self, tmp_path)
                finally:
                    self.cleanup_test()

    return wrapper


class TestWorkflowsBase(ABC):
    __test__ = False
    expect_exception = None
    omit_tmp = False
    latency_wait = 5

    @abstractmethod
    def get_executor(self) -> str:
        ...

    @abstractmethod
    def get_executor_settings(self) -> Optional[ExecutorSettingsBase]:
        ...

    @abstractmethod
    def get_default_storage_provider(self) -> Optional[str]:
        ...

    @abstractmethod
    def get_default_storage_prefix(self) -> Optional[str]:
        ...

    @abstractmethod
    def get_default_storage_provider_settings(
        self,
    ) -> Optional[Mapping[str, TaggedSettings]]:
        ...

    def get_assume_shared_fs(self) -> bool:
        return True

    def get_envvars(self) -> List[str]:
        return []

    def cleanup_test(self):
        """This method is called after every testcase, also in case of exceptions.

        Override to clean up any test files (e.g. in remote storage).
        """
        pass

    def _run_workflow(self, test_name, tmp_path, deployment_method=frozenset()):
        test_path = Path(__file__).parent / "testcases" / test_name
        if self.omit_tmp:
            tmp_path = test_path
        else:
            tmp_path = Path(tmp_path) / test_name
            self._copy_test_files(test_path, tmp_path)

        if self._common_settings().local_exec:
            cores = 3
            nodes = None
        else:
            cores = 1
            nodes = 3

        with api.SnakemakeApi(
            settings.OutputSettings(
                verbose=True,
                show_failed_logs=True,
            ),
        ) as snakemake_api:
            workflow_api = snakemake_api.workflow(
                resource_settings=settings.ResourceSettings(
                    cores=cores,
                    nodes=nodes,
                ),
                storage_settings=settings.StorageSettings(
                    default_storage_provider=self.get_default_storage_provider(),
                    default_storage_prefix=self.get_default_storage_prefix(),
                    assume_shared_fs=self.get_assume_shared_fs(),
                ),
                storage_provider_settings=self.get_default_storage_provider_settings(),
                workdir=Path(tmp_path),
                snakefile=tmp_path / "Snakefile",
            )

            dag_api = workflow_api.dag(
                deployment_settings=settings.DeploymentSettings(
                    deployment_method=deployment_method,
                ),
            )
            dag_api.execute_workflow(
                executor=self.get_executor(),
                executor_settings=self.get_executor_settings(),
                execution_settings=settings.ExecutionSettings(
                    latency_wait=self.latency_wait,
                ),
                remote_execution_settings=settings.RemoteExecutionSettings(
                    seconds_between_status_checks=0,
                    envvars=self.get_envvars(),
                ),
            )

    @handle_testcase
    def test_simple_workflow(self, tmp_path):
        self._run_workflow("simple", tmp_path)

    @handle_testcase
    def test_group_workflow(self, tmp_path):
        self._run_workflow("groups", tmp_path)

    def _copy_test_files(self, test_path, tmp_path):
        shutil.copytree(test_path, tmp_path)

    def _common_settings(self):
        registry = ExecutorPluginRegistry()
        return registry.get_plugin(self.get_executor()).common_settings


class TestWorkflowsLocalStorageBase(TestWorkflowsBase):
    def get_default_storage_provider(self) -> Optional[str]:
        return None

    def get_default_storage_prefix(self) -> Optional[str]:
        return None

    def get_default_storage_provider_settings(
        self,
    ) -> Optional[Mapping[str, TaggedSettings]]:
        return None


class TestWorkflowsMinioPlayStorageBase(TestWorkflowsBase):
    def __init__(self):
        import uuid
        from snakemake_storage_plugin_s3 import StorageProviderSettings

        endpoint_url = "https://play.minio.io:9000"
        access_key = "Q3AM3UQ867SPQQA43P2F"
        secret_key = "zuf+tfteSlswRu7BJ86wekitnifILbZam1KYY3TG"
        self._bucket = f"snakemake-{uuid.uuid4().hex}"

        self._storage_provider_settings = StorageProviderSettings(
            endpoint_url=endpoint_url,
            access_key=access_key,
            secret_key=secret_key,
        )

    def get_default_storage_provider(self) -> Optional[str]:
        return "s3"

    def get_default_storage_prefix(self) -> Optional[str]:
        return f"s3://{self._bucket}"

    def get_default_storage_provider_settings(
        self,
    ) -> Optional[Mapping[str, TaggedSettings]]:
        tagged_settings = TaggedSettings()
        tagged_settings.register_settings(self._storage_provider_settings)
        return {"s3": tagged_settings}

    def cleanup_test(self):
        import boto3

        # clean up using boto3
        s3c = boto3.resource(
            "s3",
            endpoint_url=self._storage_provider_settings.endpoint_url,
            aws_access_key_id=self._storage_provider_settings.access_key,
            aws_secret_access_key=self._storage_provider_settings.secret_key,
        )
        try:
            s3c.Bucket(self._bucket).delete()
        except Exception:
            pass