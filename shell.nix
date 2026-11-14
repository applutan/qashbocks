{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  name = "qashbocks-dev-shell";

  buildInputs = [
    pkgs.python3
    pkgs.python3Packages.jinja2
    pkgs.python3Packages.pytest
    pkgs.python3Packages.setuptools
    pkgs.sqlite
  ];

  shellHook = ''
    export PYTHONPATH="$PWD:$PYTHONPATH"
    echo "=========================================================="
    echo "  Qashbocks Nix Development Shell (nix-shell)"
    echo "  Python $(python3 --version) ready."
    echo "=========================================================="
  '';
}
