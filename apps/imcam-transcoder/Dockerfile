ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        gir1.2-gst-plugins-bad-1.0 \
        gir1.2-gst-plugins-base-1.0 \
        gstreamer1.0-libav \
        gstreamer1.0-nice \
        gstreamer1.0-plugins-bad \
        gstreamer1.0-plugins-base \
        gstreamer1.0-plugins-good \
        gstreamer1.0-plugins-ugly \
        python3 \
        python3-aiohttp \
        python3-gi \
        python3-gst-1.0 \
        python3-websockets \
    && rm -rf /var/lib/apt/lists/*

COPY server.py /opt/imcam/server.py
COPY imcam_live_gst.py /opt/imcam/imcam_live_gst.py
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
