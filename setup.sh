#!/bin/bash

# Create a help text function
help() {
    echo "NCeu Installation Script"
    echo "========================="
    echo "This script helps install or uninstall NCeu."
    echo ""
    echo "Usage: ./setup.sh [OPTION]"
    echo ""
    echo "Options:"
    echo "  --install        Install NCeu and its dependencies"
    echo "  --uninstall      Uninstall NCeu"
    echo "  --dev            Install in development mode (editable)"
    echo "  --help           Display this help text"
    echo ""
    exit 1
}

# Check if Python is installed
check_python() {
    if ! command -v python3 &> /dev/null; then
        echo "Python 3 is required but not found. Please install Python 3."
        exit 1
    fi
    
    echo "Python 3 found: $(python3 --version)"
}

# Check if pip is installed
check_pip() {
    if ! command -v pip3 &> /dev/null; then
        echo "pip3 is required but not found. Installing pip..."
        python3 -m ensurepip --upgrade
    fi
    
    echo "pip found: $(pip3 --version)"
}

# Install NCeu
install() {
    echo "Installing NCeu..."
    
    if [ "$1" == "dev" ]; then
        echo "Installing in development mode..."
        pip3 install -e .
    else
        pip3 install .
    fi
    
    if [ $? -eq 0 ]; then
        echo "NCeu installed successfully!"
        echo ""
        echo "Next steps:"
        echo "1. Download your Google API credentials.json file"
        echo "2. Run 'nceu' to start the application"
        echo ""
    else
        echo "Installation failed. Please check the error messages above."
    fi
}

# Uninstall NCeu
uninstall() {
    echo "Uninstalling NCeu..."
    pip3 uninstall -y nceu
    echo "NCeu uninstalled."
}

# Main script
if [ "$1" == "--help" ] || [ -z "$1" ]; then
    help
elif [ "$1" == "--install" ]; then
    check_python
    check_pip
    install "normal"
elif [ "$1" == "--dev" ]; then
    check_python
    check_pip
    install "dev"
elif [ "$1" == "--uninstall" ]; then
    uninstall
else
    echo "Unknown option: $1"
    help
fi
