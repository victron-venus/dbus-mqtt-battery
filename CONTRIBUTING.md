# Contributing to dbus-mqtt-battery

Thank you for your interest in contributing!

## How to Contribute

### Reporting Bugs

1. Check existing [issues](https://github.com/victron-venus/dbus-mqtt-battery/issues) to avoid duplicates
2. Use the bug report template
3. Include:
   - Venus OS version
   - ESP32 firmware version
   - BMS model and count
   - MQTT broker details
   - Relevant logs

### Suggesting Features

1. Open a feature request issue
2. Describe the use case
3. Explain why it would benefit others

### Pull Requests

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes
4. Test on actual Venus OS hardware if possible
5. Run linter: `ruff check .`
6. Commit with clear messages
7. Push and create a Pull Request

### Code Style

- Follow PEP 8
- Use meaningful variable names
- Add comments for complex logic
- Keep functions focused and small

### Testing

- Test with actual JBD BMS hardware
- Verify MQTT communication
- Check D-Bus service registration
- Verify data in VRM Portal / GUI

## Development Setup

```bash
# Clone
git clone https://github.com/victron-venus/dbus-mqtt-battery.git
cd dbus-mqtt-battery

# Test locally (requires MQTT broker)
python3 dbus-mqtt-battery.py --broker <IP> --batteries 4
```

## Questions?

- Open a [Discussion](https://github.com/victron-venus/dbus-mqtt-battery/discussions)
- Ask on [Victron Community](https://community.victronenergy.com/)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
