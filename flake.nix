{
  description = "Beeper Minus: self-hosted voice note transcription and send later for Beeper";

  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      inherit (nixpkgs) lib;
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAll =
        f:
        lib.genAttrs systems (
          system:
          f (
            import nixpkgs {
              inherit system;
              # Beeper Server is unfree.
              config.allowUnfree = true;
              overlays = [ self.overlays.default ];
            }
          )
        );
    in
    {
      overlays.default = final: prev: {
        beeper-server = final.callPackage ./nix/beeper-server.nix { };
        beeper-minus = final.callPackage ./nix/package.nix { };
      };

      nixosModules.default = {
        imports = [ ./nix/module.nix ];
        nixpkgs.overlays = [ self.overlays.default ];
      };

      packages = forAll (
        pkgs:
        {
          default = pkgs.beeper-minus;
          inherit (pkgs) beeper-minus;
        }
        # Beeper only ships Beeper Server for x86_64.
        // lib.optionalAttrs pkgs.stdenv.hostPlatform.isx86_64 { inherit (pkgs) beeper-server; }
      );

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (p: [
              p.aiohttp
              p.mcp
              p.starlette
              p.uvicorn
            ]))
            pkgs.nodejs
            pkgs.ffmpeg-headless
          ];
        };
      });

      formatter = forAll (pkgs: pkgs.nixfmt-tree);
    };
}
