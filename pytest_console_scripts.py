from __future__ import unicode_literals

import io
import os
import subprocess
import sys
import traceback

import mock
import py.path
import pytest

if sys.version_info.major == 2:
    # We can't use io.StringIO for mocking stdout/stderr in Python 2
    # because printing byte strings to it triggers unicode errors and
    # there's code in stdlib that does that (e.g. traceback module).
    import StringIO
    StreamMock = StringIO.StringIO
else:
    StreamMock = io.StringIO


def pytest_addoption(parser):
    group = parser.getgroup('console-scripts')
    group.addoption(
        '--script-launch-mode',
        metavar='inprocess|subprocess|both',
        action='store',
        dest='script_launch_mode',
        default=None,
        help='how to run python scripts under test (default: inprocess)'
    )
    parser.addini(
        'script_launch_mode',
        'how to run python scripts under test (inprocess|subprocess|both)'
    )


def _get_mark_mode(metafunc):
    """Return launch mode as indicated by test function marker or None."""
    func = metafunc.function
    if hasattr(func, 'script_launch_mode'):
        return func.script_launch_mode.combined.args[0]


def pytest_generate_tests(metafunc):
    """Parametrize script_launch_mode fixture.

    Checks the configuration sources in this order:
    - `script_launch_mode` mark on the test,
    - `--script-launch-mode` option,
    - `script_launch_mode` configuration option in [pytest] section of the
      pyest config file.

    This process yields a value that can be one of:
    - "inprocess" -- The script will be run via loading its main function
      into the test runner process and mocking the environment.
    - "subprocess" -- The script will be run via `subprocess` module.
    - "both" -- The test will be run twice: once in inprocess mode and once
      in subprocess mode.
    - None -- Same as "inprocess".
    """
    if 'script_launch_mode' not in metafunc.fixturenames:
        return

    mark_mode = _get_mark_mode(metafunc)
    option_mode = metafunc.config.option.script_launch_mode
    config_mode = metafunc.config.getini('script_launch_mode')

    mode = mark_mode or option_mode or config_mode or 'inprocess'

    if mode in {'inprocess', 'subprocess'}:
        metafunc.parametrize('script_launch_mode', [mode])
    elif mode == 'both':
        metafunc.parametrize('script_launch_mode', ['inprocess', 'subprocess'])
    else:
        raise ValueError('Invalid script launch mode: {}'.format(mode))


class RunResult(object):
    """Result of running a script."""

    def __init__(self, returncode, stdout, stderr):
        self.success = returncode == 0
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class ScriptRunner(object):
    """Fixture for running python scripts under test."""

    def __init__(self, launch_mode, rootdir):
        assert launch_mode in {'inprocess', 'subprocess'}
        self.launch_mode = launch_mode
        self.rootdir = rootdir

    def __repr__(self):
        return '<ScriptRunner {}>'.format(self.launch_mode)

    def run(self, command, *arguments, **options):
        if self.launch_mode == 'inprocess':
            return self.run_inprocess(command, *arguments, **options)
        return self.run_subprocess(command, *arguments, **options)

    def run_inprocess(self, command, *arguments, **options):
        cmdargs = [command] + list(arguments)
        script = py.path.local(sys.executable).join('..', command)
        stdin = options.get('stdin', StreamMock())
        stdout = StreamMock()
        stderr = StreamMock()
        returncode = 0
        stdin_patch = mock.patch('sys.stdin', new=stdin)
        stdout_patch = mock.patch('sys.stdout', new=stdout)
        stderr_patch = mock.patch('sys.stderr', new=stderr)
        argv_patch = mock.patch('sys.argv', new=cmdargs)
        saved_dir = os.getcwd()
        if 'cwd' in options:
            os.chdir(options['cwd'])
        with stdin_patch, stdout_patch, stderr_patch, argv_patch:
            try:
                compiled = compile(script.read(), str(script), 'exec', flags=0)
                exec(compiled, {'__name__': '__main__'})
            except SystemExit as exc:
                returncode = exc.code
                if isinstance(returncode, str):
                    stderr.write('{}\n'.format(exc))
                    returncode = 1
                elif returncode is None:
                    returncode = 0
            except Exception as exc:
                returncode = 1
                try:
                    et, ev, tb = sys.exc_info()
                    # Hide current frame from the stack trace.
                    traceback.print_exception(et, ev, tb.tb_next)
                finally:
                    del tb
        os.chdir(saved_dir)
        return RunResult(returncode, stdout.getvalue(), stderr.getvalue())

    def run_subprocess(self, command, *arguments, **options):
        stdin = ''
        if 'stdin' in options:
            stdin = options['stdin'].read()
            options['stdin'] = subprocess.PIPE
        if 'universal_newlines' not in options:
            options['universal_newlines'] = True
        p = subprocess.Popen([command] + list(arguments),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             **options)
        stdout, stderr = p.communicate(stdin)
        return RunResult(p.returncode, stdout, stderr)


@pytest.fixture
def script_launch_mode(request):
    return request.param


@pytest.fixture
def script_cwd(tmpdir):
    return tmpdir.mkdir('script-cwd')


@pytest.fixture
def script_runner(script_cwd, script_launch_mode):
    return ScriptRunner(script_launch_mode, script_cwd)
