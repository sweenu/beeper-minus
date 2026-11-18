{
  lib,
  python3Packages,
  buildNpmPackage,
  ffmpeg-headless,
}:

let
  version = "0.1.0";

  web = buildNpmPackage {
    pname = "beeper-minus-web";
    inherit version;
    src = lib.fileset.toSource {
      root = ../web;
      fileset = lib.fileset.difference ../web (
        lib.fileset.unions [
          (lib.fileset.maybeMissing ../web/node_modules)
          (lib.fileset.maybeMissing ../web/dist)
        ]
      );
    };
    npmDepsHash = "sha256-eJAUgPTSk8qyFNUBkn4ccOAD8prHE/ZFYvK6Qhtx1FI=";
    # The formatting allowlist that keeps message HTML harmless.
    doCheck = true;
    checkPhase = "npm test";
    installPhase = "cp -r dist $out";
  };
in
python3Packages.buildPythonApplication {
  pname = "beeper-minus";
  inherit version;
  pyproject = true;

  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ../pyproject.toml
      ../beeper_minus
      ../tests
    ];
  };

  build-system = [ python3Packages.setuptools ];
  dependencies = [ python3Packages.aiohttp ];

  nativeCheckInputs = [ ffmpeg-headless ];
  checkPhase = ''
    runHook preCheck
    python -m tests.test_app
    runHook postCheck
  '';

  makeWrapperArgs = [
    "--set-default"
    "BEEPER_MINUS_STATIC"
    "${web}"
    # Decodes every voice note before transcription.
    "--prefix"
    "PATH"
    ":"
    (lib.makeBinPath [ ffmpeg-headless ])
  ];

  passthru = { inherit web; };

  meta = {
    description = "Self-hosted voice note transcription and send later for Beeper";
    homepage = "https://github.com/sweenu/beeper-minus";
    license = lib.licenses.mit;
    mainProgram = "beeper-minus";
  };
}
