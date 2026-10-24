#define PY_SSIZE_T_CLEAN
#include "launcher_config.h"
#include <Python.h>

#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#ifndef PENPAL_PYTHON_EXECUTABLE
#error "PENPAL_PYTHON_EXECUTABLE must name the installed Penpal environment's Python"
#endif
#ifndef PENPAL_FALLBACK_EXECUTABLE
#error "PENPAL_FALLBACK_EXECUTABLE must name the installed penpal command"
#endif

int main(int argc, char **argv) {
    if (argc > 0 && argv[0] != NULL) {
        (void)setenv("PENPAL_HUD_NATIVE_LAUNCHER", argv[0], 1);
    }

    /* The environment moved (e.g. an upgrade replaced it): run the installed command instead of failing silently. */
    if (access(PENPAL_PYTHON_EXECUTABLE, X_OK) != 0) {
        char **fallback_argv = calloc((size_t)argc + 2, sizeof(char *));
        if (fallback_argv != NULL) {
            fallback_argv[0] = (char *)PENPAL_FALLBACK_EXECUTABLE;
            fallback_argv[1] = "hud";
            for (int i = 1; i < argc; ++i) {
                fallback_argv[i + 1] = argv[i];
            }
            execv(PENPAL_FALLBACK_EXECUTABLE, fallback_argv);
        }
        fputs("Penpal could not find its Python environment; reinstall with `penpal app install`.\n", stderr);
        return 1;
    }

    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    config.parse_argv = 1;

    PyStatus status = PyConfig_SetBytesString(&config, &config.program_name, PENPAL_PYTHON_EXECUTABLE);
    if (PyStatus_Exception(status)) {
        PyConfig_Clear(&config);
        Py_ExitStatusException(status);
        return 1;
    }
    status = PyConfig_SetBytesString(&config, &config.executable, PENPAL_PYTHON_EXECUTABLE);
    if (PyStatus_Exception(status)) {
        PyConfig_Clear(&config);
        Py_ExitStatusException(status);
        return 1;
    }

    int python_argc = argc + 3;
    char **python_argv = calloc((size_t)python_argc, sizeof(char *));
    if (python_argv == NULL) {
        PyConfig_Clear(&config);
        fputs("Penpal could not allocate Python arguments.\n", stderr);
        return 1;
    }
    python_argv[0] = (char *)PENPAL_PYTHON_EXECUTABLE;
    python_argv[1] = "-m";
    python_argv[2] = "penpal";
    python_argv[3] = "hud";
    for (int i = 1; i < argc; ++i) {
        python_argv[i + 3] = argv[i];
    }
    status = PyConfig_SetBytesArgv(&config, python_argc, python_argv);
    free(python_argv);
    if (PyStatus_Exception(status)) {
        PyConfig_Clear(&config);
        Py_ExitStatusException(status);
        return 1;
    }

    status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) {
        Py_ExitStatusException(status);
        return 1;
    }
    return Py_RunMain();
}
