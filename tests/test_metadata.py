import pytest

from aqt.metadata import QtRepoProperty, Version, get_semantic_version


@pytest.mark.parametrize(
    "input_version, is_preview, expected",
    [
        # Test cases for non-preview versions
        ("51212", False, Version("5.12.12")),
        ("600", False, Version("6.0.0")),
        ("6_7_3", False, Version("6.7.3")),
        ("6_7", False, Version("6.7.0")),
        # Test cases for preview versions
        ("51212", True, Version("5.1212-preview")),
        ("600", True, Version("6.0-preview")),
        ("6_7_3", True, Version("6.73-preview")),
        ("6_7", True, Version("6.7-preview")),
    ],
)
def test_get_semantic_version_valid(input_version, is_preview, expected):
    """
    Test the get_semantic_version function with valid inputs.

    Args:
        input_version (str): Input version string to be converted
        is_preview (bool): Flag indicating whether this is a preview version
        expected (Version): Expected semantic version output
    """
    result = get_semantic_version(input_version, is_preview)
    assert result == expected, (
        f"Failed for input '{input_version}' with is_preview={is_preview}. Expected '{expected}', but got '{result}'"
    )


@pytest.mark.parametrize(
    "invalid_input, is_preview",
    [
        ("", False),  # Empty string
        ("abc", False),  # Non-numeric input
        ("1_2_3_4", False),  # Too many underscores
        ("1_a_2", False),  # Non-numeric parts
        (None, False),  # None input
    ],
)
def test_get_semantic_version_returns_none(invalid_input, is_preview):
    """
    Test cases where the function should return None.
    """
    result = get_semantic_version(invalid_input, is_preview)
    assert result is None, f"Expected None for invalid input '{invalid_input}', but got '{result}'"


def test_get_semantic_version_raises_value_error():
    """
    Test the specific case that raises ValueError - single digit version.
    """
    with pytest.raises(ValueError, match="Invalid version string '1'"):
        get_semantic_version("1", False)


@pytest.mark.parametrize(
    "target, folder, version, own_host, expected",
    [
        # The folders of Qt 6.12's builds
        ("desktop", "macos", "6.12.0", "mac", ("mac", "clang_64")),
        ("desktop", "gcc_64", "6.12.0", "linux", ("linux", "linux_gcc_64")),
        ("desktop", "gcc_arm64", "6.12.0", "linux", ("linux_arm64", "linux_gcc_arm64")),
        ("desktop", "msvc2022_64", "6.12.0", "windows", ("windows", "win64_msvc2022_64")),
        ("desktop", "msvc2022_arm64", "6.12.0", "windows", ("windows_arm64", "win64_msvc2022_arm64")),
        ("desktop", "mingw_64", "6.12.0", "windows", ("windows", "win64_mingw")),
        ("desktop", "llvm-mingw_64", "6.12.0", "windows", ("windows", "win64_llvm_mingw")),
        ("android", "android_arm64_v8a", "6.12.0", "mac", ("all_os", "android_arm64_v8a")),
        ("android", "android_armv7", "6.12.0", "linux", ("all_os", "android_armv7")),
        ("android", "android_x86_64", "6.12.0", "windows", ("all_os", "android_x86_64")),
        ("android", "android_x86", "6.12.0", "mac", ("all_os", "android_x86")),
        ("ios", "ios_device", "6.12.0", "mac", ("mac", "ios_device")),
        ("ios", "ios_simulator_arm64", "6.12.0", "mac", ("mac", "ios_simulator_arm64")),
        ("ios", "ios_simulator_x86_64", "6.12.0", "mac", ("mac", "ios_simulator_x86_64")),
        ("wasm", "wasm_singlethread", "6.12.0", "linux", ("all_os", "wasm_singlethread")),
        ("wasm", "wasm_multithread", "6.7.3", "mac", ("all_os", "wasm_multithread")),
        # Older versions, whose folders or names differ
        ("desktop", "gcc_64", "6.7.0", "linux", ("linux", "linux_gcc_64")),
        ("desktop", "gcc_64", "6.6.3", "linux", ("linux", "gcc_64")),
        ("desktop", "macos", "6.1.2", "mac", ("mac", "clang_64")),
        ("desktop", "clang_64", "6.1.1", "mac", ("mac", "clang_64")),
        ("desktop", "clang_64", "5.15.2", "mac", ("mac", "clang_64")),
        ("desktop", "msvc2022_arm64", "6.8.0", "windows", ("windows_arm64", "win64_msvc2022_arm64")),
        ("desktop", "msvc2019_arm64", "6.7.3", "windows", ("windows", "win64_msvc2019_arm64")),
        ("desktop", "msvc2019_64", "6.7.3", "windows", ("windows", "win64_msvc2019_64")),
        ("desktop", "msvc2019", "5.15.2", "windows", ("windows", "win32_msvc2019")),
        ("desktop", "mingw81_64", "5.15.2", "windows", ("windows", "win64_mingw81")),
        ("desktop", "mingw81_32", "5.15.2", "windows", ("windows", "win32_mingw81")),
        ("desktop", "mingw53_32", "5.12.12", "windows", ("windows", "win32_mingw53")),
        ("desktop", "wasm_32", "6.2.4", "linux", ("linux", "wasm_32")),
        ("desktop", "wasm_singlethread", "6.6.3", "mac", ("mac", "wasm_singlethread")),
        ("winrt", "winrt_x64_msvc2019", "5.15.2", "windows", ("windows", "win64_msvc2019_winrt_x64")),
        ("winrt", "winrt_x86_msvc2019", "5.15.2", "windows", ("windows", "win64_msvc2019_winrt_x86")),
        ("winrt", "msvc2019_winrt_armv7", "5.15.2", "windows", ("windows", "win64_msvc2019_winrt_armv7")),
        ("android", "android_armv7", "6.6.3", "linux", ("linux", "android_armv7")),
        ("android", "android", "5.15.2", "mac", ("mac", "android")),
        ("android", "android_x86", "5.13.2", "windows", ("windows", "android_x86")),
        ("ios", "ios", "6.11.2", "mac", ("mac", "ios")),
        ("ios", "ios", "5.15.2", "linux", ("mac", "ios")),
        # Folders that no build of the target and version goes into
        ("desktop", "macos", "6.1.1", "mac", None),
        ("desktop", "clang_64", "6.12.0", "mac", None),
        ("desktop", "android_arm64_v8a", "6.12.0", "linux", None),
        ("desktop", "ios", "6.12.0", "mac", None),
        ("desktop", "msvc22_64", "6.12.0", "windows", None),
        ("desktop", "unknown", "6.12.0", "linux", None),
        ("android", "gcc_arm64", "6.12.0", "linux", None),
        ("ios", "macos", "6.12.0", "mac", None),
        ("ios", "android_x86", "6.12.0", "mac", None),
        ("wasm", "gcc_64", "6.12.0", "linux", None),
        ("winrt", "msvc2019_64", "5.15.2", "windows", None),
        ("qt", "gcc_64", "6.12.0", "linux", None),
    ],
)
def test_get_host_and_arch(target, folder, version, own_host, expected):
    version = Version(version)
    assert QtRepoProperty.get_host_and_arch(target, folder, version, own_host) == expected
    if expected:
        # The names lead back to the folder
        assert QtRepoProperty.get_arch_dir_name(*expected, version) == folder


@pytest.mark.parametrize(
    "host, target, version, arches",
    [
        # What Qt's repository listed in November 2026
        ("windows", "desktop", "6.12.0", ["win64_mingw", "win64_llvm_mingw", "win64_msvc2022_64"]),
        ("windows_arm64", "desktop", "6.12.0", ["win64_msvc2022_arm64"]),
        ("linux", "desktop", "6.12.0", ["linux_gcc_64"]),
        ("linux_arm64", "desktop", "6.12.0", ["linux_gcc_arm64"]),
        ("mac", "desktop", "6.12.0", ["clang_64"]),
        ("windows", "desktop", "6.7.3", ["win64_llvm_mingw", "win64_mingw", "win64_msvc2019_64", "win64_msvc2019_arm64"]),
        ("linux", "desktop", "6.6.3", ["gcc_64", "wasm_singlethread", "wasm_multithread"]),
        ("windows", "desktop", "5.15.2", ["win32_mingw81", "win32_msvc2019", "win64_mingw81", "win64_msvc2019_64"]),
        ("windows", "winrt", "5.15.2", ["win64_msvc2019_winrt_armv7", "win64_msvc2019_winrt_x64"]),
        ("all_os", "android", "6.12.0", ["android_armv7", "android_arm64_v8a", "android_x86_64", "android_x86"]),
        ("linux", "android", "6.6.3", ["android_armv7", "android_x86", "android_arm64_v8a", "android_x86_64"]),
        ("mac", "ios", "6.12.0", ["ios_device", "ios_simulator_arm64", "ios_simulator_x86_64"]),
        ("all_os", "wasm", "6.12.0", ["wasm_singlethread", "wasm_multithread"]),
    ],
)
def test_get_host_and_arch_undoes_get_arch_dir_name(host, target, version, arches):
    version = Version(version)
    own_host = "linux" if host == "all_os" else host
    for arch in arches:
        folder = QtRepoProperty.get_arch_dir_name(host, arch, version)
        assert QtRepoProperty.get_host_and_arch(target, folder, version, own_host) == (host, arch)


def test_get_host_and_arch_takes_the_native_arm64_build():
    # Qt 6.8 and later install the ARM64 build cross compiled on an x64 machine into the folder of
    # the native one, so the folder names the native build
    version = Version("6.12.0")
    folder = QtRepoProperty.get_arch_dir_name("windows", "win64_msvc2022_arm64_cross_compiled", version)
    assert folder == "msvc2022_arm64"
    assert QtRepoProperty.get_host_and_arch("desktop", folder, version, "windows") == (
        "windows_arm64",
        "win64_msvc2022_arm64",
    )
