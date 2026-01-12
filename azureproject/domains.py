"""
Centralized domain configuration for multi-domain routing.

All domain-related settings should reference this file to maintain
a single source of truth for domain configuration.

Usage:
    from azureproject.domains import DOMAINS, DEV_DEFAULT, get_domain_config

To test a different site locally, change DEV_DEFAULT:
    DEV_DEFAULT = 'vinsdelux.com'  # or 'entreprinder.lu'
"""

DOMAINS = {
    'delegations.lu': {
        'urlconf': 'azureproject.urls_delegations',
        'name': 'Delegations.lu',
        'app': 'delegations',
        'aliases': ['www.delegations.lu'],
    },
    'crush.lu': {
        'urlconf': 'azureproject.urls_crush',
        'name': 'Crush.lu',
        'app': 'crush_lu',
        'aliases': ['www.crush.lu'],
    },
    'vinsdelux.com': {
        'urlconf': 'azureproject.urls_vinsdelux',
        'name': 'VinsDelux',
        'app': 'vinsdelux',
        'aliases': ['www.vinsdelux.com'],
    },
    'entreprinder.lu': {
        'urlconf': 'azureproject.urls_entreprinder',
        'name': 'Entreprinder',
        'app': 'entreprinder',
        'aliases': ['www.entreprinder.lu'],
    },
    'power-up.lu': {
        'urlconf': 'azureproject.urls_power_up',
        'name': 'Power-Up',
        'app': 'power_up',
        'aliases': ['www.power-up.lu', 'powerup.lu', 'www.powerup.lu'],
    },
    'tableau.lu': {
        'urlconf': 'azureproject.urls_tableau',
        'name': 'Tableau',
        'app': 'tableau',
        'aliases': ['www.tableau.lu'],
    },
}

# Development configuration
# Note: 'testserver' is added for pytest-django live_server fixture
DEV_HOSTS = ['localhost', '127.0.0.1', '192.168.178.184', 'testserver']
DEV_DEFAULT = 'crush.lu'  # Change this to test different sites locally

# Local development domain mappings (avoids HSTS issues with real domains)
DEV_DOMAIN_MAPPINGS = {
    'crush.localhost': 'crush.lu',
    'power-up.localhost': 'power-up.lu',
    'powerup.localhost': 'power-up.lu',
    'vinsdelux.localhost': 'vinsdelux.com',
    'entreprinder.localhost': 'entreprinder.lu',
    'tableau.localhost': 'tableau.lu',
    'delegation.localhost': 'delegations.lu',
}

# Production fallback (used for unknown domains and Azure hostnames)
PRODUCTION_DEFAULT = 'entreprinder.lu'


def get_domain_config(host):
    """
    Get configuration for a domain, checking both primary domains and aliases.

    Args:
        host: The HTTP host (may include port)

    Returns:
        Domain config dict if found, None otherwise
    """
    host = host.split(':')[0].lower()

    # Direct match
    if host in DOMAINS:
        return DOMAINS[host]

    # Check aliases (e.g., www.crush.lu)
    for domain, config in DOMAINS.items():
        if host in config.get('aliases', []):
            return config

    return None


def get_urlconf_for_host(host):
    """
    Get the URL configuration module for a given host.

    Args:
        host: The HTTP host (may include port)

    Returns:
        URL configuration module path string
    """
    host = host.split(':')[0].lower()

    # Try to get domain config
    config = get_domain_config(host)
    if config:
        return config['urlconf']

    # Check dev domain mappings (e.g., crush.localhost -> crush.lu)
    if host in DEV_DOMAIN_MAPPINGS:
        mapped_domain = DEV_DOMAIN_MAPPINGS[host]
        return DOMAINS[mapped_domain]['urlconf']

    # Development hosts
    if host in DEV_HOSTS:
        return DOMAINS[DEV_DEFAULT]['urlconf']

    # Azure App Service hostname
    if host.endswith('.azurewebsites.net'):
        return DOMAINS[PRODUCTION_DEFAULT]['urlconf']

    # Fallback to production default
    return DOMAINS[PRODUCTION_DEFAULT]['urlconf']


def get_all_hosts():
    """
    Get all valid hosts for ALLOWED_HOSTS configuration.

    Returns:
        List of all domain names and aliases
    """
    hosts = list(DOMAINS.keys())
    for config in DOMAINS.values():
        hosts.extend(config.get('aliases', []))
    hosts.extend(DEV_HOSTS)
    # Add dev domain mappings (*.localhost)
    hosts.extend(DEV_DOMAIN_MAPPINGS.keys())
    return hosts


def is_valid_domain(host):
    """
    Check if a host is a valid configured domain.

    Args:
        host: The HTTP host to check

    Returns:
        True if the host is configured, False otherwise
    """
    return get_domain_config(host) is not None
