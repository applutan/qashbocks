{
  description = "Qashbocks - Fast, standalone personal finance CLI ledger and forecasting engine";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python311;
      in
      rec {
        packages.qashbocks = python.pkgs.buildPythonApplication {
          pname = "qashbocks";
          version = "0.1.0";
          src = ./.;
          format = "pyproject";

          nativeBuildInputs = [
            python.pkgs.setuptools
          ];

          propagatedBuildInputs = [
            python.pkgs.jinja2
          ];

          # Include presentation templates and sample database
          postInstall = ''
            mkdir -p $out/share/qashbocks
            cp -r templates $out/share/qashbocks/
            cp -r tools $out/share/qashbocks/
            cp -r data $out/share/qashbocks/
          '';

          meta = with pkgs.lib; {
            description = "Fast, standalone personal finance command-line ledger and cash-flow forecasting engine";
            homepage = "https://github.com/applutan/qashbocks";
            license = licenses.mit;
            mainProgram = "qashbocks";
          };
        };

        packages.default = packages.qashbocks;

        apps.default = flake-utils.lib.mkApp {
          drv = packages.qashbocks;
        };

        devShells.default = pkgs.mkShell {
          buildInputs = [
            python
            python.pkgs.jinja2
            python.pkgs.pytest
            python.pkgs.setuptools
            pkgs.sqlite
          ];

          shellHook = ''
            export PYTHONPATH="$PWD:$PYTHONPATH"
            echo "=========================================================="
            echo "  Qashbocks Nix Development Environment"
            echo "  Run 'bin/qashbocks accounts' or 'pytest tests/' to start."
            echo "=========================================================="
          '';
        };
      }
    );
}
