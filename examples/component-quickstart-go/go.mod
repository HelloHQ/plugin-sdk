module github.com/HelloHQ/plugin-sdk/examples/component-quickstart-go

go 1.24

require github.com/HelloHQ/plugin-sdk/sdks/go v0.0.0

require go.bytecodealliance.org/cm v0.3.0 // indirect

// The SDK is not published; build against the in-repo copy.
replace github.com/HelloHQ/plugin-sdk/sdks/go => ../../sdks/go
