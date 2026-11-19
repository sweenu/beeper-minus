{
  lib,
  stdenv,
  fetchurl,
  autoPatchelfHook,
  makeWrapper,
  gnutar,
  gzip,
}:

stdenv.mkDerivation (finalAttrs: {
  pname = "beeper-server";
  # Feed: https://api.beeper.com/desktop/update-feed.json?bundleID=com.automattic.beeper.server&platform=linux&channel=stable&arch=x64
  version = "4.3.332";

  src = fetchurl {
    url = "https://beeper-desktop.download.beeper.com/builds/beeper-server-${finalAttrs.version}-linux-x64.tar.gz";
    hash = "sha512-wMHsjcUdWCyJHDHEc2q+9il5vTtLs2CE/k8jyw66KX0m2NuGGRKHD3MDPeEHxOVry3M6A/kE020qkNJhB2Olqw==";
  };

  nativeBuildInputs = [
    autoPatchelfHook
    makeWrapper
  ];
  buildInputs = [ stdenv.cc.cc.lib ];

  # A Node single-executable app: stripping would drop the embedded app blob.
  dontStrip = true;

  installPhase = ''
    runHook preInstall
    install -Dm755 beeper-server $out/libexec/beeper-server
    # The launcher shells out to tar to unpack its app on first start.
    makeWrapper $out/libexec/beeper-server $out/bin/beeper-server \
      --prefix PATH : ${lib.makeBinPath [ gnutar gzip ]}
    runHook postInstall
  '';

  meta = {
    description = "Headless Beeper client serving the Beeper Client API";
    homepage = "https://www.beeper.com";
    license = lib.licenses.unfree;
    platforms = [ "x86_64-linux" ];
    mainProgram = "beeper-server";
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
  };
})
