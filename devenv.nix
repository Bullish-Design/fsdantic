{ inputs, pkgs, config, ... }:

let
  lib = pkgs.lib;
  root = config.git.root;

  system = pkgs.stdenv.system;

  # Fenix nightly toolchain profile (complete = nightly complete profile)
  fenixToolchain = inputs.fenix.packages.${system}.complete;

  # Add the nixpkgs rustc metadata that buildRustPackage expects
  fenixRustcForBuildRustPackage =
    fenixToolchain.rustc
    // {
      targetPlatforms = pkgs.rustc.targetPlatforms;
      badTargetPlatforms = pkgs.rustc.badTargetPlatforms or [ ];
    };

  agentfsRustPlatform = pkgs.makeRustPlatform {
    cargo = fenixToolchain.cargo;
    rustc = fenixRustcForBuildRustPackage;
  };


  envOrDefault = name: default:
    let v = builtins.getEnv name;
    in if v != "" then v else default;
 
  # IMPORTANT: use a path literal, not "${root}/..."
  agentfsPath = ./.devman/store/vendor/agentfs;  # NOTE: this is a path on the host, not in the Nix store, so we don't use pkgs.path

  agentfsSrc = builtins.path {
    path = agentfsPath;
    name = "agentfs-src";
  };

  # Pure-safe: derived from the evaluated devenv config, not host env
  agentfsEnabled = (config.env.AGENTFS_ENABLED or "1") != "0";
  agentfsEnabledLabel = if agentfsEnabled then "yes" else "no";

  cargoToml = builtins.fromTOML (builtins.readFile (agentfsSrc + "/cli/Cargo.toml"));

  
  agentfsPackage = agentfsRustPlatform.buildRustPackage {
    pname = cargoToml.package.name;
    inherit (cargoToml.package) version;
    src = agentfsSrc;


    preCheck = ''
      export LD_LIBRARY_PATH=${pkgs.lib.makeLibraryPath [
        pkgs.openssl
        # if anything else shows up later, add it here
      ]}:$LD_LIBRARY_PATH
    '';

    postFixup = ''
      # Ensure installed binaries can find OpenSSL at runtime (no LD_LIBRARY_PATH needed)
      for bin in $out/bin/*; do
        if [ -x "$bin" ] && file "$bin" | grep -q ELF; then
          patchelf --add-rpath ${pkgs.lib.makeLibraryPath [ pkgs.openssl ]} "$bin" || true
        fi
      done
    '';

    cargoLock.lockFile = agentfsSrc + "/cli/Cargo.lock";
    cargoLock.outputHashes = {
      "reverie-0.1.0" = "sha256-TxjOCsH2vPwwqG+19ByyRsf4tkSn6/xXzuHxkNWgnek=";
    };

    buildAndTestSubdir = "cli";
    postUnpack = "cp $sourceRoot/cli/Cargo.lock $sourceRoot/Cargo.lock";
    buildNoDefaultFeatures = !pkgs.stdenv.isLinux;

    nativeBuildInputs = [ pkgs.pkg-config ];
    buildInputs = with pkgs; [ openssl ]
      ++ pkgs.lib.optionals pkgs.stdenv.isLinux [ fuse3 libunwind ];

    doCheck = false;
  
  };


  agentfsServe = ''
    cd "${toString root}"
    mkdir -p "$AGENTFS_DATA_DIR"
    exec ${pkgs.turso-cli}/bin/turso agentfs serve \
      --host "$AGENTFS_HOST" \
      --port "$AGENTFS_PORT" \
      --data-dir "$AGENTFS_DATA_DIR" \
      --db "$AGENTFS_DB_NAME" \
      --log-level "$AGENTFS_LOG_LEVEL" \
      $AGENTFS_EXTRA_ARGS
  '';

in
{
  packages = with pkgs; [
    git
    curl
    turso-cli
    agentfsPackage

    # QoL tooling
    jq
    ripgrep
    just
  ];

  env = {
    PROJECT_NAME = "fsdantic";
    # Default can be overridden by your shell env (AGENTFS_ENABLED=0) or by devenv config.
    AGENTFS_ENABLED = lib.mkDefault (envOrDefault "AGENTFS_ENABLED" "1");

    AGENTFS_HOST = lib.mkDefault "127.0.0.1";
    AGENTFS_PORT = lib.mkDefault "8081";

    # Keep state anchored under the repo root (works no matter where you run from).
    AGENTFS_DATA_DIR = lib.mkDefault "${root}/.devenv/state/agentfs";

    AGENTFS_DB_NAME = lib.mkDefault "sandbox";
    AGENTFS_LOG_LEVEL = lib.mkDefault "info";
    AGENTFS_EXTRA_ARGS = lib.mkDefault "";
  };

  processes.agentfs = lib.mkIf agentfsEnabled {
    exec = agentfsServe;
  };

  # Stable command interfaces live here; workflow tweaks can live elsewhere.
  #scripts.agentfs.exec = agentfsServe;

  scripts.agentfs-info.exec = ''
    cd "${root}"
    cat <<INFO
AgentFS process configuration
-----------------------------
Repo root: ${root}
Enabled:   $AGENTFS_ENABLED (shell), ${agentfsEnabledLabel} (eval)
Host:      $AGENTFS_HOST
Port:      $AGENTFS_PORT
Data dir:  $AGENTFS_DATA_DIR
DB name:   $AGENTFS_DB_NAME
Log level: $AGENTFS_LOG_LEVEL
Extra:     $AGENTFS_EXTRA_ARGS
INFO
  '';

  scripts.agentfs-url.exec = ''
    echo "http://$AGENTFS_HOST:$AGENTFS_PORT"
  '';

  scripts.agentfs-cli.exec = ''
    exec ${agentfsPackage}/bin/agentfs "$@"
  '';

  scripts.link-abs-to-repo.exec = ''
    exec uv run --script ./scripts/link_abs_to_repo.py /home/andrew/Documents/Projects/vendor/"$@" ./.devenv/store/vendor/"$@"
'';

  scripts.link-agentfs.exec = ''
    exec link-abs-to-repo agentfs
  '';

  scripts.agentfs-session-create.exec = ''
    cd "${root}"
    export NIXBOX_ROOT="${root}"
    export AGENTFS_BIN="${agentfsPackage}/bin/agentfs"
    exec uv run --script ./scripts/agentfs_session_create.py "$@"
  '';

  scripts.agentfs-session-shell.exec = ''
    cd "${root}"
    export NIXBOX_ROOT="${root}"
    export AGENTFS_BIN="${agentfsPackage}/bin/agentfs"
    exec uv run --script ./scripts/agentfs_session_shell.py "$@"
  '';

  scripts.agentfs-session-boot.exec = ''
    cd "${root}"
    export NIXBOX_ROOT="${root}"
    export AGENTFS_BIN="${agentfsPackage}/bin/agentfs"
    exec uv run --script ./scripts/agentfs_session_boot.py "$@"
  '';

  # # Test runner scripts
  # scripts.test.exec = ''
  #   cd "${root}"
  #   exec uv run --all-extras pytest "$@"
  # '';

  # scripts.test-unit.exec = ''
  #   cd "${root}"
  #   exec uv run --all-extras pytest agentfs-pydantic/tests/test_models.py "$@"
  # '';

  # scripts.test-integration.exec = ''
  #   cd "${root}"
  #   exec uv run --all-extras pytest agentfs-pydantic/tests/test_integration.py "$@"
  # '';

  # scripts.test-performance.exec = ''
  #   cd "${root}"
  #   exec uv run --all-extras pytest -m benchmark agentfs-pydantic/tests/test_performance.py "$@"
  # '';

  # scripts.test-property.exec = ''
  #   cd "${root}"
  #   exec uv run --all-extras pytest agentfs-pydantic/tests/test_property_based.py "$@"
  # '';

  # scripts.test-cov.exec = ''
  #   cd "${root}"
  #   exec uv run --all-extras pytest \
  #     --cov=agentfs-pydantic/src/agentfs_pydantic \
  #     --cov-report=term-missing \
  #     --cov-report=html \
  #     --cov-branch \
  #     agentfs-pydantic/tests/ "$@"
  # '';

  # scripts.test-fast.exec = ''
  #   cd "${root}"
  #   exec uv run --all-extras pytest -m "not slow and not benchmark" "$@"
  # '';

  enterShell = ''
    echo
    echo --------------------------------------------------------
    echo
    echo " AgentFS development environment "
    echo
    echo " Useful commands:"
    echo "   agentfs-info   # show current AgentFS config"
    echo "   agentfs-url    # print the base URL"
    echo "   agentfs-cli    # run the upstream agentfs CLI"
    echo "   agentfs        # run AgentFS in the foreground"
    echo "   devenv up       # run background processes (includes agentfs)"
    echo
    echo --------------------------------------------------------
    echo
    cd "${root}"
    echo "PWD: $(pwd)"
    echo
    agentfs-info
    echo
    echo
  '';
}
