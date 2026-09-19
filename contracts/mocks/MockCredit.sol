// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "../interfaces/IERC20.sol";

/// @title MockCredit
/// @notice A bare-bones ERC-20 with 6 decimals, matching Orbio's documented
///         CREDIT precision ("USDG and CREDIT use 6 decimals"). Used ONLY
///         for the testnet rehearsal of CollateralVault before any real
///         CREDIT touches mainnet. Not audited, not for any deployment
///         that holds real value — `mint` is unrestricted by design so the
///         hackathon demo can self-fund on testnet without a faucet for
///         this specific token.
contract MockCredit is IERC20 {
    string public constant name = "Mock Orbio CREDIT";
    string public constant symbol = "mCREDIT";
    uint8 public constant decimals = 6;

    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    /// @notice Unrestricted mint — testnet rehearsal only, see contract notice above.
    function mint(address to, uint256 amount) external {
        totalSupply += amount;
        balanceOf[to] += amount;
        emit Transfer(address(0), to, amount);
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        _transfer(msg.sender, to, amount);
        return true;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        uint256 allowed = allowance[from][msg.sender];
        require(allowed >= amount, "MockCredit: insufficient allowance");
        if (allowed != type(uint256).max) {
            allowance[from][msg.sender] = allowed - amount;
        }
        _transfer(from, to, amount);
        return true;
    }

    function _transfer(address from, address to, uint256 amount) internal {
        require(to != address(0), "MockCredit: transfer to zero address");
        uint256 bal = balanceOf[from];
        require(bal >= amount, "MockCredit: insufficient balance");
        unchecked {
            balanceOf[from] = bal - amount;
        }
        balanceOf[to] += amount;
        emit Transfer(from, to, amount);
    }
}
