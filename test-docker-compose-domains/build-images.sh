#!/bin/sh

/bin/echo -e "### Build owkin image\n"
docker build owkin -t test-domain-owkin
/bin/echo
/bin/echo -e "### Build chunantes image\n"
docker build chunantes -t test-domain-chunantes