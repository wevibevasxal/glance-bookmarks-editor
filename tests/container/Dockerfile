# Stand-in Glance host (Debian 13 + systemd + Glance 0.8.7), set up like the Proxmox VE helper script.
# Test-only; never used in production.
FROM debian:trixie
ENV container=docker DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends systemd systemd-sysv ca-certificates iproute2 procps python3 \
 && rm -rf /var/lib/apt/lists/* \
 && systemctl mask getty@tty1.service systemd-logind.service console-getty.service
COPY glance /opt/glance/glance
COPY glance.service /etc/systemd/system/glance.service
COPY env.conf /etc/systemd/system/glance.service.d/env.conf
RUN chmod 755 /opt/glance/glance && systemctl enable glance
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
