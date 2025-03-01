import os
import pytest

from ansible_runner import defaults
from ansible_runner.interface import run, run_async, run_command, run_command_async, get_plugin_docs, \
    get_plugin_docs_async, get_plugin_list, get_ansible_config, get_inventory


def test_run():
    r = run(module='debug', host_pattern='localhost')
    assert r.status == 'successful'


@pytest.mark.parametrize(
    'playbook', (
        [{'hosts': 'localhost', 'tasks': [{'ping': ''}]}],
        {'hosts': 'localhost', 'tasks': [{'ping': ''}]},
    )
)
def test_run_playbook_data(playbook, tmp_path):
    r = run(private_data_dir=str(tmp_path), playbook=playbook)
    assert r.status == 'successful'


def test_run_async(tmp_path):
    thread, r = run_async(private_data_dir=str(tmp_path), module='debug', host_pattern='localhost')
    thread.join()
    assert r.status == 'successful'


def get_env_data(res):
    for event in res.events:
        found = bool(
            event['event'] == 'runner_on_ok' and event.get(
                'event_data', {}
            ).get('task_action', None) == 'look_at_environment'
        )
        if found:
            return event['event_data']['res']
    else:
        print('output:')
        print(res.stdout.read())
        raise RuntimeError('Count not find look_at_environment task from playbook')


def test_env_accuracy(request, project_fixtures):
    printenv_example = project_fixtures / 'printenv'
    os.environ['SET_BEFORE_TEST'] = 'MADE_UP_VALUE'

    def remove_test_env_var():
        if 'SET_BEFORE_TEST' in os.environ:
            del os.environ['SET_BEFORE_TEST']

    request.addfinalizer(remove_test_env_var)

    res = run(
        private_data_dir=printenv_example,
        playbook='get_environment.yml',
        inventory=None,
        envvars={'FROM_TEST': 'FOOBAR'},
    )
    assert res.rc == 0, res.stdout.read()

    actual_env = get_env_data(res)['environment']

    assert actual_env == res.config.env


@pytest.mark.test_all_runtimes
def test_env_accuracy_inside_container(request, project_fixtures, runtime):
    printenv_example = project_fixtures / 'printenv'
    os.environ['SET_BEFORE_TEST'] = 'MADE_UP_VALUE'

    def remove_test_env_var():
        if 'SET_BEFORE_TEST' in os.environ:
            del os.environ['SET_BEFORE_TEST']

    request.addfinalizer(remove_test_env_var)

    res = run(
        private_data_dir=printenv_example,
        project_dir='/tmp',
        playbook='get_environment.yml',
        inventory=None,
        envvars={'FROM_TEST': 'FOOBAR'},
        settings={
            'process_isolation_executable': runtime,
            'process_isolation': True
        }
    )
    assert res.rc == 0, res.stdout.read()

    env_data = get_env_data(res)
    actual_env = env_data['environment']

    expected_env = res.config.env.copy()

    # NOTE: the reported environment for containerized jobs will not account for
    # all environment variables, particularly those set by the entrypoint script
    for key, value in expected_env.items():
        assert key in actual_env
        assert actual_env[key] == value, 'Reported value wrong for {0} env var'.format(key)

    assert env_data['cwd'] == res.config.cwd


def test_multiple_inventories(project_fixtures):
    private_data_dir = project_fixtures / 'debug'

    res = run(
        private_data_dir=private_data_dir,
        playbook='debug.yml',
    )
    stdout = res.stdout.read()
    assert res.rc == 0, stdout

    # providing no inventory should cause <private_data_dir>/inventory
    # to be used, reading both inventories in the directory
    assert 'host_1' in stdout
    assert 'host_2' in stdout


def test_inventory_absolute_path(project_fixtures):
    private_data_dir = project_fixtures / 'debug'

    res = run(
        private_data_dir=private_data_dir,
        playbook='debug.yml',
        inventory=[
            str(private_data_dir / 'inventory' / 'inv_1'),
        ],
    )
    stdout = res.stdout.read()
    assert res.rc == 0, stdout

    # hosts can be down-selected to one inventory out of those available
    assert 'host_1' in stdout
    assert 'host_2' not in stdout


def test_run_command(project_fixtures):
    private_data_dir = project_fixtures / 'debug'
    inventory = private_data_dir / 'inventory' / 'inv_1'
    playbook = private_data_dir / 'project' / 'debug.yml'
    out, err, rc = run_command(
        private_data_dir=private_data_dir,
        executable_cmd='ansible-playbook',
        cmdline_args=[str(playbook), '-i', str(inventory)]
    )
    assert "Hello world!" in out
    assert rc == 0
    assert err == ''


def test_run_command_injection_error():
    out, err, rc = run_command(
        executable_cmd='whoami',
        cmdline_args=[';hostname'],
        runner_mode='subprocess',
    )
    assert rc == 1
    assert "usage: whoami" in err or "whoami: extra operand ‘;hostname’" in err


@pytest.mark.test_all_runtimes
def test_run_command_injection_error_within_container(runtime):
    out, err, rc = run_command(
        executable_cmd='whoami',
        cmdline_args=[';hostname'],
        runner_mode='subprocess',
        process_isolation_executable=runtime,
        process_isolation=True,
        container_image=defaults.default_container_image,
    )
    assert rc == 1
    assert "whoami: extra operand ';hostname'" in err


@pytest.mark.test_all_runtimes
def test_run_ansible_command_within_container(project_fixtures, runtime):
    private_data_dir = project_fixtures / 'debug'
    inventory = private_data_dir / 'inventory' / 'inv_1'
    playbook = private_data_dir / 'project' / 'debug.yml'
    container_kwargs = {
        'process_isolation_executable': runtime,
        'process_isolation': True,
        'container_image': defaults.default_container_image
    }
    out, err, rc = run_command(
        private_data_dir=private_data_dir,
        executable_cmd='ansible-playbook',
        cmdline_args=[str(playbook), '-i', str(inventory)],
        **container_kwargs
    )
    assert "Hello world!" in out
    assert rc == 0
    assert err == ''


@pytest.mark.test_all_runtimes
def test_run_script_within_container(project_fixtures, runtime):
    private_data_dir = project_fixtures / 'debug'
    script_path = project_fixtures / 'files'
    container_volume_mounts = ["{}:{}:Z".format(script_path, script_path)]
    container_kwargs = {
        'process_isolation_executable': runtime,
        'process_isolation': True,
        'container_image': defaults.default_container_image,
        'container_volume_mounts': container_volume_mounts
    }
    out, _, rc = run_command(
        private_data_dir=private_data_dir,
        executable_cmd='python3',
        cmdline_args=[str(script_path / 'test_ee.py')],
        **container_kwargs
    )

    assert "os-release" in out
    assert rc == 0


def test_run_command_async(project_fixtures):
    private_data_dir = project_fixtures / 'debug'
    inventory = private_data_dir / 'inventory' / 'inv_1'
    playbook = private_data_dir / 'project' / 'debug.yml'
    thread, r = run_command_async(
        private_data_dir=private_data_dir,
        executable_cmd='ansible-playbook',
        cmdline_args=[str(playbook), '-i', str(inventory)]
    )
    thread.join()
    out = r.stdout.read()
    assert "Hello world!" in out
    assert r.status == 'successful'


def test_get_plugin_docs():
    out, _ = get_plugin_docs(
        plugin_names=['file', 'copy'],
        plugin_type='module',
        quiet=True
    )
    assert 'copy' in out
    assert 'file' in out


def test_get_plugin_docs_async():
    thread, r = get_plugin_docs_async(
        plugin_names=['file', 'copy'],
        plugin_type='module',
        quiet=True
    )
    thread.join()
    out = r.stdout.read()
    assert 'copy' in out
    assert 'file' in out
    assert r.status == 'successful'


@pytest.mark.test_all_runtimes
def test_get_plugin_docs_within_container(runtime):
    container_kwargs = {
        'process_isolation_executable': runtime,
        'process_isolation': True,
        'container_image': defaults.default_container_image
    }
    out, _ = get_plugin_docs(
        plugin_names=['file', 'copy'],
        plugin_type='module',
        quiet=True,
        **container_kwargs
    )
    assert 'copy' in out
    assert 'file' in out


def test_get_plugin_docs_list():
    out, _ = get_plugin_list(
        list_files=True,
        quiet=True
    )
    assert 'copy' in out
    assert 'file' in out


@pytest.mark.test_all_runtimes
def test_get_plugin_docs_list_within_container(runtime):
    container_kwargs = {
        'process_isolation_executable': runtime,
        'process_isolation': True,
        'container_image': defaults.default_container_image
    }
    out, _ = get_plugin_list(
        list_files=True,
        quiet=True,
        **container_kwargs
    )
    assert 'copy' in out
    assert 'file' in out


def test_ansible_config():
    out, _ = get_ansible_config(
        action='list',
        quiet=True
    )
    assert 'DEFAULT_VERBOSITY' in out


def test_get_inventory(project_fixtures):
    private_data_dir = project_fixtures / 'debug'
    inventory1 = private_data_dir / 'inventory' / 'inv_1'
    inventory2 = private_data_dir / 'inventory' / 'inv_2'

    out, _ = get_inventory(
        action='list',
        inventories=[str(inventory1), str(inventory2)],
        response_format='json',
        quiet=True
    )
    assert 'host_1' in out['ungrouped']['hosts']
    assert 'host_2' in out['ungrouped']['hosts']


@pytest.mark.test_all_runtimes
def test_get_inventory_within_container(project_fixtures, runtime):
    container_kwargs = {
        'process_isolation_executable': runtime,
        'process_isolation': True,
        'container_image': defaults.default_container_image
    }
    private_data_dir = project_fixtures / 'debug'
    inventory1 = private_data_dir / 'inventory' / 'inv_1'
    inventory2 = private_data_dir / 'inventory' / 'inv_2'

    out, _ = get_inventory(
        action='list',
        inventories=[str(inventory1), str(inventory2)],
        response_format='json',
        quiet=True,
        **container_kwargs
    )
    assert 'host_1' in out['ungrouped']['hosts']
    assert 'host_2' in out['ungrouped']['hosts']


def test_run_role(project_fixtures):
    ''' Test that we can run a role via the API. '''
    private_data_dir = project_fixtures / 'debug'

    res = run(
        private_data_dir=private_data_dir,
        role='hello_world',
    )
    stdout = res.stdout.read()
    assert res.rc == 0, stdout
    assert 'Hello World!' in stdout
