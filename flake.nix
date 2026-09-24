{
  # The non-Python side of the toolchain. Python itself is uv's business:
  # .python-version names the interpreter, uv fetches it (it runs on NixOS
  # through nix-ld), and uv.lock pins every package. This shell supplies what
  # uv cannot: uv, ffmpeg, lua 5.1 (luac -p, textkey_parity.py) and the shared
  # libraries the manylinux and CUDA wheels expect to find on the system.
  #
  #   nix develop -c uv run tools/generate.py      # what ./tools/run.sh does
  description = "Forever Voiceover tools: uv, ffmpeg, lua and shared libraries";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forEachSystem = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      devShells = forEachSystem (pkgs: {
        default = pkgs.mkShell {
          packages = with pkgs; [ uv ffmpeg lua5_1 ];

          env = {
            # Never a system Python, even if one is on PATH: only the one uv manages.
            UV_PYTHON_PREFERENCE = "only-managed";
            PYTHONUNBUFFERED = "1";
            TQDM_DISABLE = "1";
          };

          shellHook = ''
            # numpy, torch and friends need libstdc++ and zlib; the CUDA wheels
            # need the host's libcuda, which NixOS keeps in /run/opengl-driver/lib.
            libs=${pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib pkgs.zlib ]}:/run/opengl-driver/lib
            export LD_LIBRARY_PATH="$libs''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
          '';
        };
      });
    };
}
