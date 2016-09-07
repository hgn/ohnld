tips:
	@echo
	@echo "now install and setup configuration files, prefered in /etc/ohnld/"
	@echo "Edit /lib/systemd/system/ohnld@.service and point to the configuration file(s)"
	@echo "now call sudo systemctl daemon-reload"
	@echo ".. enable service via: sudo systemctl enable ohnld@FOO.service"
	@echo ".. start service via:  sudo systemctl start ohnld@FOO.service"
	@echo ".. status via:         sudo systemctl status ohnld@FOO.service"
	@echo ".. log info via:       sudo journalctl -u ohnld@FOO.service"

install_deps:
	sudo -H pip3 install -r requirements.txt

install:
	install -m 755 -T ohnld.py /usr/bin/ohnld
	install -m 644 assets/ohnld@.service /lib/systemd/system/
	mkdir -p /etc/ohnld
	make tips

uninstall:
	rm -rf /usr/bin/ohnld
	rm -rf /lib/systemd/system/ohnld@.service

