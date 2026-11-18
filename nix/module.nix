{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib)
    getExe
    mkDefault
    mkEnableOption
    mkIf
    mkOption
    mkPackageOption
    optional
    types
    ;
  server = config.services.beeper-server;
  cfg = config.services.beeper-minus;
  toml = pkgs.formats.toml { };
  configFile = toml.generate "beeper-minus.toml" cfg.settings;
  engine = cfg.settings.transcription.engine or null;
in
{
  options.services.beeper-server = {
    enable = mkEnableOption "Beeper Server, a headless Beeper client serving the Beeper Client API";
    package = mkPackageOption pkgs "beeper-server" { };
    host = mkOption {
      type = types.str;
      default = "127.0.0.1";
      description = "Address the Beeper Client API listens on.";
    };
    port = mkOption {
      type = types.port;
      default = 23373;
      description = "Port the Beeper Client API listens on.";
    };
  };

  options.services.beeper-minus = {
    enable = mkEnableOption "Beeper Minus: voice note transcription and send later for Beeper";
    package = mkPackageOption pkgs "beeper-minus" { };
    whisperPackage = mkPackageOption pkgs "whisper-cpp" {
      example = "whisper-cpp-vulkan";
      extraDescription = "Provides `whisper-cli` for the `whisper-cpp` transcription engine.";
    };
    settings = mkOption {
      inherit (toml) type;
      default = { };
      description = ''
        Beeper Minus configuration, written to its TOML config file.
        See config.example.toml for every key.
      '';
      example = lib.literalExpression ''
        {
          beeper.token_file = config.age.secrets.beeperToken.path;
          web.port = 8099;
          transcription = {
            engine = "whisper-cpp";
            auto = true;
            notes_to_self = true;
            whisper-cpp.model = "''${model}";
          };
        }
      '';
    };
  };

  config = lib.mkMerge [
    (mkIf (server.enable || cfg.enable) {
      users.users.beeper = {
        isSystemUser = true;
        group = "beeper";
        home = "/var/lib/beeper";
      };
      users.groups.beeper = { };
    })

    (mkIf server.enable {
      # One-time login goes through POST /v1/app/setup/* (email code + device verification).
      systemd.services.beeper-server = {
        description = "Beeper Server";
        wantedBy = [ "multi-user.target" ];
        wants = [ "network-online.target" ];
        after = [ "network-online.target" ];
        environment = {
          HOME = "/var/lib/beeper";
          BEEPER_SERVER_DATA_DIR = "/var/lib/beeper/server";
          # The binary unpacks its ~370 MB app here on first start.
          XDG_CACHE_HOME = "/var/cache/beeper";
          HOST = server.host;
          PORT = toString server.port;
        };
        serviceConfig = {
          ExecStart = getExe server.package;
          User = "beeper";
          Group = "beeper";
          StateDirectory = "beeper";
          StateDirectoryMode = "0700";
          CacheDirectory = "beeper";
          Restart = "always";
          RestartSec = "10s";
        };
      };
    })

    (mkIf cfg.enable {
      services.beeper-minus.settings.beeper.url =
        mkDefault "http://${server.host}:${toString server.port}";

      systemd.services.beeper-minus = {
        description = "Beeper Minus";
        wantedBy = [ "multi-user.target" ];
        wants = [ "network-online.target" ] ++ optional server.enable "beeper-server.service";
        after = [ "network-online.target" ] ++ optional server.enable "beeper-server.service";
        path = optional (engine == "whisper-cpp") cfg.whisperPackage;
        serviceConfig = {
          ExecStart = "${getExe cfg.package} ${configFile}";
          User = "beeper";
          Group = "beeper";
          StateDirectory = "beeper-minus";
          StateDirectoryMode = "0700";
          Restart = "always";
          RestartSec = "10s";
          NoNewPrivileges = true;
          PrivateTmp = true;
          ProtectHome = true;
          ProtectSystem = "strict";
        };
      };
    })
  ];
}
