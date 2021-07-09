""" Cookie cutter wrapper used to template out components
"""
import os
import glob
import sys
from pathlib import Path
from typing import Dict
import shutil

from cookiecutter.main import cookiecutter
from cookiecutter.exceptions import OutputDirExistsException
from jinja2 import Environment, FileSystemLoader

from fprime.fbuild.builder import Build, Target
from fprime.fbuild.cmake import (
    CMakeExecutionException,
    CMakeHandler,
    CMakeInvalidBuildException,
)
from fprime.fbuild.settings import IniSettings


def confirm(msg):
    """Confirms the removal of the file with a yes or no input"""
    # Loop "forever" intended
    while True:
        confirm_input = input(msg)
        if confirm_input.lower() in ["y", "yes"]:
            return True
        if confirm_input.lower() in ["n", "no"]:
            return False
        print("{} is invalid.  Please use 'yes' or 'no'".format(confirm_input))


def run_impl(deployment: Path, path: Path, platform: str, verbose: bool):
    """Run implementation of files one time"""
    target = Target.get_target("impl", set())
    build = Build(target.build_type, deployment, verbose=verbose)
    build.load(path, platform)

    hpp_files = glob.glob("{}/*.hpp".format(path), recursive=False)
    cpp_files = glob.glob("{}/*.cpp".format(path), recursive=False)
    cpp_files.sort(key=len)

    # Check destinations
    if not hpp_files or not cpp_files:
        print(
            "[WARNING] Failed to find .cpp and .hpp destination files for implementation."
        )
        return False

    common = [name for name in cpp_files if "Common" in name] + []
    hpp_dest = hpp_files[0]
    cpp_dest = common[0] if common else cpp_files[0]

    if not confirm(
        "Generate implementations and merge into {} and {}?".format(hpp_dest, cpp_dest)
    ):
        return False
    build.execute(target, context=path, make_args={})

    hpp_files_template = glob.glob("{}/*.hpp-template".format(path), recursive=False)
    cpp_files_template = glob.glob("{}/*.cpp-template".format(path), recursive=False)

    if not hpp_files_template or not cpp_files_template:
        print("[WARNING] Failed to find generated .cpp-template or .hpp-template files")
        return False

    hpp_src = hpp_files_template[0]
    cpp_src = cpp_files_template[0]

    # Copy files without headers
    for src, dest in [(hpp_src, hpp_dest), (cpp_src, cpp_dest)]:
        with open(src, "r") as file_handle:
            lines = file_handle.readlines()
        lines = lines[11:]  # Remove old header
        with open(dest, "a") as file_handle:
            file_handle.write("".join(lines))

    removals = [Path(hpp_src), Path(cpp_src)]
    for removal in removals:
        os.remove(removal)
    return True


def add_to_cmake(list_file: Path, comp_path: Path):
    """ Adds new component or port to CMakeLists.txt"""
    print("[INFO] Found CMakeLists.txt at '{}'".format(list_file))
    with open(list_file, "r") as f:
        lines = f.readlines()
        index = 0
        while "add_fprime_subdirectory" not in lines[index]:
            index += 1
        while "add_fprime_subdirectory" in lines[index]:
            index += 1

    if not confirm(
        "Add component {} to {} {}?".format(
            comp_path, list_file, "at end of file before topology inclusion?"
        )
    ):
        return False

    addition = (
        'add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/' + str(comp_path) + '/")\n'
    )
    if addition in lines:
        print("Component already added to CMakeLists.txt")
        return True
    lines.insert(index, addition)
    with open(list_file, "w") as f:
        f.write("".join(lines))
    return True


def regenerate(cmake_list_file):
    # Ensures build directory exists, then refreshes build cache
    build_dir = Path(cmake_list_file.parent, "build-fprime-automatic-native")
    if not os.path.isdir(build_dir):
        raise CMakeInvalidBuildException(build_dir)
    handler = CMakeHandler()
    print("Refreshing cache to include new addition")
    handler._cmake_refresh_cache(build_dir)


def add_unit_tests(deployment, comp_path, platform, verbose):
    # Creates unit tests and moves them into test/ut directory
    os.chdir(str(comp_path))
    if confirm("Would you like to generate unit tests?: "):
        target = Target.get_target("impl", {"ut"})
        build = Build(target.build_type, deployment, verbose=verbose)
        build.load(comp_path, platform)
        build.execute(target, context=comp_path, make_args={})
        test_files = [
            "Tester.hpp",
            "Tester.cpp",
            "TesterBase.hpp",
            "TesterBase.cpp",
            "GTestBase.hpp",
            "GTestBase.cpp",
            "TestMain.cpp",
        ]
        for file in test_files:
            if os.path.isfile(file):
                new_name = Path("test", "ut", file)
                os.rename(file, str(new_name))
    else:
        shutil.rmtree("test")


def add_port_to_cmake(list_file: Path, comp_path: Path):
    """ Adds new port to CMakeLists.txt in port directory"""
    print("[INFO] Found CMakeLists.txt at '{}'".format(list_file))
    with open(list_file, "r") as file_handle:
        lines = file_handle.readlines()
    index = 0
    line = lines[index]
    while line != "set(SOURCE_FILES\n":
        index += 1
        line = lines[index]
    index += 1
    if not confirm(
        "Add component {} to {} {}?".format(
            comp_path, list_file, "ports in CMakeLists.txt"
        )
    ):
        return False

    addition = '"${{CMAKE_CURRENT_LIST_DIR}}/{}/")\n'.format(comp_path)
    lines.insert(index, addition)
    with open(list_file, "w") as file_handle:
        file_handle.write("".join(lines))
    return True


def find_nearest_cmake_lists(component_dir: Path, deployment: Path, proj_root: Path):
    """Find the nearest CMakeLists.txt file

    The "nearest" file is defined as the closes parent that is not the "project root" that contains a CMakeLists.txt. If
    none is found, the same procedure is run from the deployment directory and includes the project root this time. If
    nothing is found, None is returned.

    In short the following in order of preference:
     - Any Component Parent
     - Any Deployment Parent
     - Project Root
     - None

    Args:
        component_dir: directory of new component
        deployment: deployment directory
        proj_root: project root directory

    Returns:
        path to CMakeLists.txt or None
    """
    test_path = component_dir.parent
    # First iterate from where we are, then from the deployment to find the nearest CMakeList.txt nearby
    for test_path, end_path in [(test_path, proj_root), (deployment, proj_root.parent)]:
        while proj_root is not None and test_path != proj_root.parent:
            cmake_list_file = test_path / "CMakeLists.txt"
            if cmake_list_file.is_file():
                return cmake_list_file
            test_path = test_path.parent
    return None


def new_component(
    deployment: Path, platform: str, verbose: bool, settings: Dict[str, str]
):
    """Uses cookiecutter for making new components"""
    try:
        print("[WARNING] **** fprime-util new is prototype functionality ****")
        proj_root = None
        try:
            proj_root = Path(settings.get("project_root", None))
        except (ValueError, TypeError):
            print("[WARNING] No found project root.")

        # Checks if cookiecutter is set in settings.ini file, else uses local cookiecutter template as default
        if (
            settings.get("cookiecutter") is not None
            and settings["cookiecutter"] != "default"
        ):
            source = settings["cookiecutter"]
        else:
            source = (
                os.path.dirname(__file__)
                + "/../cookiecutter_templates/cookiecutter-fprime-component"
            )

        print("[INFO] Cookiecutter source: {}".format(source))
        print()
        print("----------------")
        print(
            "[INFO] Help available here: https://github.com/SterlingPeet/cookiecutter-fprime-component/blob/master/README.rst#id3"
        )
        print("----------------")
        print()
        final_component_dir = Path(cookiecutter(source)).resolve()
        if proj_root is None:
            print(
                "[INFO] Created component directory without adding to build system nor generating implementation {}".format(
                    final_component_dir
                )
            )
            return 0
        # Attempt to register to CMakeLists.txt
        cmake_lists_file = find_nearest_cmake_lists(
            final_component_dir, deployment, proj_root
        )
        if cmake_lists_file is None or not add_to_cmake(
            cmake_lists_file, final_component_dir.relative_to(cmake_lists_file.parent)
        ):
            print(
                "[INFO] Could not register {} with build system. Please add it and generate implementations manually.".format(
                    final_component_dir
                )
            )
            return 0
        regenerate(cmake_lists_file)
        # Attempt implementation
        if not run_impl(deployment, final_component_dir, platform, verbose):
            print(
                "[INFO] Could not generate implementations. Please do so manually.".format(
                    final_component_dir
                )
            )
            return 0
        print("[INFO] Created new component and created initial implementations.")
        add_unit_tests(deployment, final_component_dir, platform, verbose)
        print(
            "[INFO] Next run `fprime-util build{}` in {}".format(
                "" if platform is None else " " + platform, final_component_dir
            )
        )
        return 0
    except OutputDirExistsException as out_directory_error:
        print("{}".format(out_directory_error), file=sys.stderr)
    except CMakeExecutionException as exc:
        print("[ERROR] Failed to create component. {}".format(exc), file=sys.stderr)
    except OSError as ose:
        print("[ERROR] {}".format(ose))
    return 1


def is_valid_name(word):
    invalid_characters = [
        "#",
        "%",
        "&",
        "{",
        "}",
        "/",
        "\\",
        "<",
        ">",
        "*",
        "?",
        " ",
        "$",
        "!",
        "'",
        '"',
        ":",
        "@",
        "+",
        "`",
        "|",
        "=",
    ]
    for char in invalid_characters:
        if char in word:
            return char
    return "valid"


def get_port_input():
    # Gather inputs to use as context for the port template
    defaults = {
        "port_name": "ExamplePort",
        "short_description": "Example usage of port",
        "dir_name": "example_directory",
        "arg_number": 1,
    }
    valid_name = False
    valid_dir = False
    while not valid_name:
        port_name = input("Port Name [{}]: ".format(defaults["port_name"]))
        char = is_valid_name(port_name)
        if char != "valid":
            print("'" + char + "' is not a valid character. Enter a new port name:")
        else:
            valid_name = True
    short_description = input(
        "Short Description [{}]: ".format(defaults["short_description"])
    )
    while not valid_dir:
        dir_name = input("Directory Name [{}]: ".format(defaults["dir_name"]))
        char = is_valid_name(dir_name)
        if char != "valid":
            print(
                "'" + char + "' is not a valid character. Enter a new directory name:"
            )
        else:
            valid_dir = True
    string_arg_number = input(
        "Number of arguments [{}]: ".format(defaults["arg_number"])
    )
    if string_arg_number == "":
        arg_number = 1
    elif not string_arg_number.isnumeric():
        print("[ERROR] You have not entered a valid number")
    else:
        arg_number = int(string_arg_number)
    values = {
        "port_name": port_name,
        "short_description": short_description,
        "dir_name": dir_name,
        "arg_number": arg_number,
    }

    # Fill in blank values with defaults
    for key in values:
        if values[key] == "":
            values[key] = defaults[key]
    return values


def make_namespace(deployment, cwd):
    # Form the namespace from the path to the deployment
    namespace_path = cwd.relative_to(deployment)
    deployment_dir = deployment.name
    whole_path = Path(deployment_dir, namespace_path)
    namespace = whole_path.parent
    namespace_formatted = str(namespace).replace(os.path.sep, "::")
    return namespace_formatted


def new_port(cwd: Path, deployment: Path, settings: Dict[str, str]):
    """ Uses cookiecutter for making new ports """
    try:
        print("[WARNING] **** fprime-util new is prototype functionality ****")
        proj_root = None
        try:
            proj_root = Path(settings.get("project_root", None))
        except (ValueError, TypeError):
            print("[WARNING] No found project root.")

        PATH = os.path.dirname(os.path.abspath(__file__))
        TEMPLATE_ENVIRONMENT = Environment(
            autoescape=False,
            loader=FileSystemLoader(os.path.join(PATH, "../cookiecutter_templates")),
            trim_blocks=False,
        )
        params = get_port_input()
        if (
            settings.get("framework_path") is not None
            and settings["framework_path"] != "native"
        ):
            path_to_fprime = settings["framework_path"]
        else:
            path_to_fprime = IniSettings.find_fprime(cwd)

        namespace = make_namespace(
            deployment, Path(str(cwd) + "/" + params["dir_name"])
        )

        context = {
            "port_name": params["port_name"],
            "short_description": params["short_description"],
            "dir_name": params["dir_name"],
            "path_to_fprime_root": path_to_fprime,
            "namespace": namespace,
            "arg_number": params["arg_number"],
        }
        fname = context["port_name"] + "Port" + "Ai.xml"
        with open(fname, "w") as f:
            xml_file = TEMPLATE_ENVIRONMENT.get_template("port_template.xml").render(
                context
            )
            f.write(xml_file)
        if not os.path.isdir(context["dir_name"]):
            os.mkdir(context["dir_name"])

        os.rename(fname, context["dir_name"] + "/" + fname)
        if not os.path.isfile(context["dir_name"] + "/CMakeLists.txt"):
            with open(context["dir_name"] + "/CMakeLists.txt", "w") as f:
                CMake_file = TEMPLATE_ENVIRONMENT.get_template(
                    "CMakeLists_template.txt"
                ).render(context)
                f.write(CMake_file)
        else:
            add_port_to_cmake(context["dir_name"] + "/CMakeLists.txt", fname)

        if proj_root is None:
            print(
                "[INFO] No project root found. Created port without adding to build system nor generating implementation."
            )
            return 0
        cmake_lists_file = find_nearest_cmake_lists(
            Path(context["dir_name"]).resolve(), deployment, proj_root
        )
        if cmake_lists_file is None or not add_to_cmake(
            cmake_lists_file,
            (Path(context["dir_name"]).resolve()).relative_to(cmake_lists_file.parent),
        ):
            print(
                "[INFO] Could not register {} with build system. Please add it and generate implementations manually.".format(
                    Path(context["dir_name"]).resolve()
                )
            )
            return 0
        regenerate(cmake_lists_file)
        print("")
        print(
            "################################################################################"
        )
        print("")
        print(
            "You have successfully created the port "
            + context["port_name"]
            + " located in "
            + context["dir_name"]
        )
        print("")
        print(
            "################################################################################"
        )
        return 0
    except OutputDirExistsException as out_directory_error:
        print("{}".format(out_directory_error), file=sys.stderr)
    except CMakeExecutionException as exc:
        print("[ERROR] Failed to create port. {}".format(exc), file=sys.stderr)
    except OSError as ose:
        print("[ERROR] {}".format(ose))
    return 1
