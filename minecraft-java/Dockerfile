ARG BUILD_FROM
FROM ${BUILD_FROM}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG MC_IMAGE_TAG
LABEL io.hass.version.base-image="${MC_IMAGE_TAG}"

# hadolint ignore=DL3002
USER root

# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash curl jq tar gzip coreutils findutils procps python3 python3-yaml \
    && rm -rf /var/lib/apt/lists/*

RUN case "$(dpkg --print-architecture)" in \
      amd64) playit_asset="playit-linux-amd64" ;; \
      arm64) playit_asset="playit-linux-aarch64" ;; \
      *) echo "Unsupported architecture for playit: $(dpkg --print-architecture)" >&2; exit 1 ;; \
    esac \
    && curl -fsSL "https://github.com/playit-cloud/playit-agent/releases/latest/download/${playit_asset}" -o /usr/local/bin/playit \
    && chmod +x /usr/local/bin/playit

COPY rootfs /

RUN sed -i 's/\r$//' \
      /usr/local/bin/ha-minecraft \
      /usr/local/lib/ha-minecraft/common.sh \
      /usr/local/lib/ha-minecraft/backup.sh \
      /usr/local/lib/ha-minecraft/bedrock.py \
      /usr/local/lib/ha-minecraft/security.py \
      /usr/local/lib/ha-minecraft/options_config.py \
      /usr/local/lib/ha-minecraft/player_model.py \
      /usr/local/lib/ha-minecraft/mqtt_discovery.py \
      /usr/local/lib/ha-minecraft/plugin_browser.py \
      /usr/local/share/ha-minecraft/home-assistant/minecraft_addon_config_sensor.py \
      /usr/local/share/ha-minecraft/home-assistant/minecraft_server_sensor.py \
    && chmod +x \
      /usr/local/bin/ha-minecraft \
      /usr/local/lib/ha-minecraft/common.sh \
      /usr/local/lib/ha-minecraft/backup.sh \
      /usr/local/lib/ha-minecraft/bedrock.py \
      /usr/local/lib/ha-minecraft/security.py \
      /usr/local/lib/ha-minecraft/options_config.py \
      /usr/local/lib/ha-minecraft/player_model.py \
      /usr/local/lib/ha-minecraft/mqtt_discovery.py \
      /usr/local/lib/ha-minecraft/plugin_browser.py \
      /usr/local/share/ha-minecraft/home-assistant/minecraft_addon_config_sensor.py \
      /usr/local/share/ha-minecraft/home-assistant/minecraft_server_sensor.py

WORKDIR /media/minecraft-java
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD test -f /tmp/ha-minecraft-controller-ready || exit 1
ENTRYPOINT ["/usr/local/bin/ha-minecraft"]
