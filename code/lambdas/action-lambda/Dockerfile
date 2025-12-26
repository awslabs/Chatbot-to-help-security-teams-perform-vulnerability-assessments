# Upgrade to Python 3.12 for latest security patches
FROM public.ecr.aws/lambda/python:3.12
RUN dnf update --security -y && dnf clean all
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --upgrade pip setuptools wheel --no-cache-dir
RUN pip install -r requirements.txt --no-cache-dir
RUN pip install --pre torch==2.1.2+cpu --no-cache-dir --index-url https://download.pytorch.org/whl/test
COPY . ${LAMBDA_TASK_ROOT}
# Set non-root user before CMD for security
USER 1001
CMD ["index.get_response"]
HEALTHCHECK --interval=600s --timeout=2s --retries=12 \
    CMD ["cat", "requirements.txt"]
