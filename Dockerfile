FROM python:3.12-bookworm

SHELL ["/bin/bash", "-xo", "pipefail", "-c"]

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libxml2-dev \
        libxslt1-dev \
        libldap2-dev \
        libsasl2-dev \
        libssl-dev \
        libjpeg-dev \
        libffi-dev \
        zlib1g-dev \
        node-less \
        npm \
        xfonts-75dpi \
        xfonts-base \
        fontconfig \
        libx11-6 \
        libxext6 \
        libxrender1 \
        wkhtmltopdf \
        postgresql-client \
    && npm install -g rtlcss \
    && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -d /opt/odoo -s /bin/bash odoo

WORKDIR /opt/odoo

COPY requirements.txt /opt/odoo/
RUN pip install --no-cache-dir setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt

COPY . /opt/odoo/

RUN mkdir -p /var/lib/odoo/filestore /var/log/odoo /etc/odoo \
    && chown -R odoo:odoo /var/lib/odoo /var/log/odoo /etc/odoo /opt/odoo

COPY docker/odoo.conf /etc/odoo/odoo.conf
RUN chown odoo:odoo /etc/odoo/odoo.conf

EXPOSE 8069 8072

USER odoo

ENTRYPOINT ["python3", "/opt/odoo/odoo-bin"]
CMD ["-c", "/etc/odoo/odoo.conf"]
