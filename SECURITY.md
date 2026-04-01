# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 2.5.x   | :white_check_mark: |
| < 2.5   | :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability, please:

1. **Do NOT** open a public issue
2. Email the maintainers directly or use GitHub's private vulnerability reporting
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

## Security Considerations

This project runs on Venus OS with access to:

- MQTT broker (battery data)
- D-Bus (Victron system)

### Recommendations

1. **MQTT**: Use authentication on your MQTT broker
2. **Network**: Run on a trusted local network
3. **Firewall**: Restrict MQTT port (1883) access

## Known Limitations

- MQTT connection without TLS by default
- Designed for trusted home networks only
